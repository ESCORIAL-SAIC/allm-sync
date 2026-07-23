"""Wrapper de la API developer de AnythingLLM (v1).

Concentra acá todas las llamadas HTTP para que, si cambia un path entre versiones,
sólo haya que tocar este archivo. Verificá los paths contra el Swagger de tu
instancia en {base_url}/api/docs si algo falla.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

log = logging.getLogger("allm.client")


class AllmError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class AllmClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 120, max_retries: int = 4):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        )

    # ── request con backoff ante 5xx / errores de red ──────────────────────
    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base}/api/v1{path}"
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
                if resp.status_code >= 400:
                    # 5xx se reintenta; 4xx (cliente/permiso/no encontrado) no.
                    raise AllmError(
                        f"{resp.status_code} en {method} {path}: {resp.text[:300]}",
                        status=resp.status_code,
                    )
                return resp
            except (requests.RequestException, AllmError) as exc:
                last_exc = exc
                is_client_error = isinstance(exc, AllmError) and exc.status is not None and 400 <= exc.status < 500
                if attempt == self.max_retries or is_client_error:
                    break
                backoff = min(2 ** attempt, 30)
                log.warning("Reintento %d/%d %s %s tras error: %s (espera %ss)",
                            attempt, self.max_retries, method, path, exc, backoff)
                time.sleep(backoff)
        raise AllmError(f"Falló {method} {path}: {last_exc}")

    # ── health / auth ──────────────────────────────────────────────────────
    def verify_auth(self) -> bool:
        resp = self._request("GET", "/auth")
        return bool(resp.json().get("authenticated", False))

    # ── workspaces ──────────────────────────────────────────────────────────
    def list_workspaces(self) -> list[dict]:
        resp = self._request("GET", "/workspaces")
        return resp.json().get("workspaces", [])

    def create_workspace(self, name: str) -> dict:
        resp = self._request("POST", "/workspace/new", json={"name": name})
        ws = resp.json().get("workspace")
        if not ws:
            raise AllmError(f"create_workspace('{name}') no devolvió workspace: {resp.text[:300]}")
        return ws

    def update_embeddings(self, slug: str, adds: list[str] | None = None,
                          deletes: list[str] | None = None) -> None:
        body = {"adds": adds or [], "deletes": deletes or []}
        self._request("POST", f"/workspace/{slug}/update-embeddings", json=body)

    # ── documentos ────────────────────────────────────────────────────────
    def create_folder(self, name: str) -> None:
        """Crea una carpeta lógica en el storage de documentos. Idempotente en la práctica."""
        try:
            self._request("POST", "/document/create-folder", json={"name": name})
        except AllmError as exc:
            # Si ya existe, AnythingLLM suele devolver 200 con success=false; toleramos.
            log.debug("create_folder('%s'): %s", name, exc)

    def upload_document(self, file_path: str, folder: str | None = None) -> str:
        """Sube un archivo y devuelve su 'location' (ej. 'folder/nombre-uuid.json')."""
        path = f"/document/upload/{folder}" if folder else "/document/upload"
        p = Path(file_path)
        with p.open("rb") as fh:
            files = {"file": (p.name, fh, "application/octet-stream")}
            resp = self._request("POST", path, files=files)
        data = resp.json()
        if not data.get("success", True):
            raise AllmError(f"upload de '{p.name}' falló: {data.get('error')}")
        docs = data.get("documents") or []
        if not docs:
            raise AllmError(f"upload de '{p.name}' no devolvió documentos: {resp.text[:300]}")
        location = docs[0].get("location")
        if not location:
            raise AllmError(f"upload de '{p.name}' sin 'location': {resp.text[:300]}")
        return location

    def remove_documents(self, locations: list[str]) -> None:
        """Borra documentos del storage de AnythingLLM por su 'location'."""
        if not locations:
            return
        self._request("DELETE", "/system/remove-documents", json={"names": locations})
