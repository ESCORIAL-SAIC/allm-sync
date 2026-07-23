"""Reconciliación por ciclo: lleva AnythingLLM al estado del share (espejo completo)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from allm_client import AllmClient, AllmError
from scanner import ScannedFile, scan, sha256_of
from state import FileRecord, State

log = logging.getLogger("allm.reconciler")


@dataclass
class CycleStats:
    scanned: int = 0
    added: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    errors: int = 0

    def summary(self) -> str:
        return (f"escaneados={self.scanned} nuevos={self.added} modificados={self.updated} "
                f"borrados={self.deleted} sin_cambio={self.unchanged} errores={self.errors}")


class Reconciler:
    def __init__(self, cfg, client: AllmClient, state: State, heartbeat=None):
        self.cfg = cfg
        self.client = client
        self.state = state
        # callback opcional para refrescar liveness durante ciclos largos (por archivo)
        self._heartbeat = heartbeat or (lambda: None)
        # cache nombre_workspace -> (slug, doc_folder) para no repetir llamadas por ciclo
        self._ws_cache: dict[str, tuple[str, str]] = {}
        self._ws_listing_loaded = False

    def run_cycle(self) -> CycleStats:
        stats = CycleStats()
        self._ws_cache.clear()
        self._ws_listing_loaded = False

        files = scan(
            self.cfg.root_path,
            self.cfg.include_extensions,
            self.cfg.exclude_globs,
            self.cfg.exclude_top_folders,
            self.cfg.max_file_bytes,
        )
        stats.scanned = len(files)
        seen_paths: set[str] = set()

        for sf in files:
            seen_paths.add(sf.rel_path)
            try:
                self._process_file(sf, stats)
            except AllmError as exc:
                stats.errors += 1
                log.error("Error procesando %s: %s", sf.rel_path, exc)
            except OSError as exc:
                stats.errors += 1
                log.error("Error de E/S en %s: %s", sf.rel_path, exc)
            self._heartbeat()  # el ciclo inicial puede durar horas; mantener liveness

        # Borrados: filas en DB cuyo archivo ya no está en el share.
        for rel_path in self.state.all_file_paths() - seen_paths:
            try:
                self._process_delete(rel_path, stats)
            except AllmError as exc:
                stats.errors += 1
                log.error("Error borrando %s: %s", rel_path, exc)
            self._heartbeat()

        return stats

    # ── por archivo ─────────────────────────────────────────────────────────
    def _process_file(self, sf: ScannedFile, stats: CycleStats) -> None:
        existing = self.state.get_file(sf.rel_path)

        # Chequeo rápido: si size y mtime coinciden, asumimos sin cambios (no hasheamos).
        if existing and existing["size"] == sf.size and abs(existing["mtime"] - sf.mtime) < 1e-6:
            stats.unchanged += 1
            return

        # size/mtime cambiaron (o es nuevo): confirmamos con hash real.
        digest = sha256_of(sf.abs_path)
        if existing and existing["sha256"] == digest:
            # Sólo cambió mtime; contenido idéntico. Actualizamos metadata y listo.
            self._save_state(sf, digest, existing["workspace_slug"], existing["allm_doc_location"])
            stats.unchanged += 1
            return

        slug, folder = self._ensure_workspace(sf.workspace_name)

        if self.cfg.dry_run:
            if existing:
                log.info("[dry-run] ACTUALIZARÍA %s -> workspace '%s'", sf.rel_path, sf.workspace_name)
                stats.updated += 1
            else:
                log.info("[dry-run] SUBIRÍA %s -> workspace '%s'", sf.rel_path, sf.workspace_name)
                stats.added += 1
            return

        # Si es modificación, primero quitamos el documento viejo del workspace y del storage.
        if existing:
            old_loc = existing["allm_doc_location"]
            self.client.update_embeddings(existing["workspace_slug"], deletes=[old_loc])
            self.client.remove_documents([old_loc])

        location = self.client.upload_document(str(sf.abs_path), folder=folder)
        self.client.update_embeddings(slug, adds=[location])
        self._save_state(sf, digest, slug, location)

        if existing:
            stats.updated += 1
            log.info("Actualizado %s (workspace '%s')", sf.rel_path, sf.workspace_name)
        else:
            stats.added += 1
            log.info("Agregado %s (workspace '%s')", sf.rel_path, sf.workspace_name)

    def _process_delete(self, rel_path: str, stats: CycleStats) -> None:
        row = self.state.get_file(rel_path)
        if not row:
            return
        if self.cfg.dry_run:
            log.info("[dry-run] BORRARÍA %s de workspace '%s'", rel_path, row["workspace_name"])
            stats.deleted += 1
            return
        loc = row["allm_doc_location"]
        self.client.update_embeddings(row["workspace_slug"], deletes=[loc])
        self.client.remove_documents([loc])
        self.state.delete_file(rel_path)
        stats.deleted += 1
        log.info("Borrado %s (workspace '%s')", rel_path, row["workspace_name"])

    # ── helpers ──────────────────────────────────────────────────────────────
    def _save_state(self, sf: ScannedFile, digest: str, slug: str, location: str) -> None:
        self.state.upsert_file(FileRecord(
            rel_path=sf.rel_path,
            workspace_name=sf.workspace_name,
            workspace_slug=slug,
            sha256=digest,
            size=sf.size,
            mtime=sf.mtime,
            allm_doc_location=location,
        ))

    def _ensure_workspace(self, name: str) -> tuple[str, str]:
        """Devuelve (slug, doc_folder), creando el workspace/carpeta si hace falta."""
        if name in self._ws_cache:
            return self._ws_cache[name]

        # 1) ¿Ya lo conocemos en el estado local?
        cached = self.state.get_workspace(name)
        if cached:
            result = (cached["slug"], cached["doc_folder"])
            self._ws_cache[name] = result
            return result

        # 2) ¿Existe ya en AnythingLLM (por nombre)?
        slug = self._find_remote_slug(name)

        # 3) Si no existe, crearlo.
        if slug is None:
            if self.cfg.dry_run:
                log.info("[dry-run] CREARÍA workspace '%s'", name)
                slug = _slugify(name)  # placeholder para el dry-run
            else:
                ws = self.client.create_workspace(name)
                slug = ws.get("slug") or _slugify(name)
                log.info("Workspace creado: '%s' (slug=%s)", name, slug)

        doc_folder = _slugify(name)
        if not self.cfg.dry_run:
            self.client.create_folder(doc_folder)
            self.state.upsert_workspace(name, slug, doc_folder)

        result = (slug, doc_folder)
        self._ws_cache[name] = result
        return result

    def _find_remote_slug(self, name: str) -> str | None:
        listing = self.client.list_workspaces()
        for ws in listing:
            if ws.get("name") == name:
                return ws.get("slug")
        return None


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower())
    return re.sub(r"-+", "-", s).strip("-") or "workspace"
