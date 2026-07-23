"""Recorre el share y produce la lista de archivos candidatos por workspace.

Regla de mapeo: cada carpeta de PRIMER NIVEL bajo ROOT_PATH es un workspace.
Los archivos sueltos en la raíz (sin carpeta) se ignoran.
"""
from __future__ import annotations

import fnmatch
import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("allm.scanner")

# Prefijo tipo "01- ", "11 - ", "03-" al inicio del nombre de carpeta. Se saca para
# derivar el nombre del workspace (p. ej. "03- RRHH" -> "RRHH").
_PREFIX_RE = re.compile(r"^\s*\d+\s*-\s*")


def workspace_name_from_folder(folder: str) -> str:
    name = _PREFIX_RE.sub("", folder).strip()
    return name or folder


@dataclass
class ScannedFile:
    rel_path: str          # ruta relativa a ROOT_PATH, con '/' (clave estable)
    abs_path: Path
    workspace_name: str    # carpeta de primer nivel
    size: int
    mtime: float


def scan(root: str, include_ext: set[str], exclude_globs: list[str],
         exclude_top: set[str], max_bytes: int) -> list[ScannedFile]:
    root_path = Path(root)
    out: list[ScannedFile] = []

    for top in sorted(root_path.iterdir()):
        if not top.is_dir():
            continue
        if top.name in exclude_top:
            log.debug("Carpeta de primer nivel excluida: %s", top.name)
            continue
        # El workspace se nombra sin el prefijo numérico ("03- RRHH" -> "RRHH").
        workspace_name = workspace_name_from_folder(top.name)
        for f in top.rglob("*"):
            if not f.is_file():
                continue
            name = f.name
            if include_ext and f.suffix.lower().lstrip(".") not in include_ext:
                continue
            if any(fnmatch.fnmatch(name, pat) for pat in exclude_globs):
                continue
            try:
                st = f.stat()
            except OSError as exc:
                log.warning("No se pudo stat %s: %s", f, exc)
                continue
            if st.st_size > max_bytes:
                log.warning("Se saltea %s: %.1f MB supera el máximo", f, st.st_size / 1024 / 1024)
                continue
            rel = f.relative_to(root_path).as_posix()
            out.append(ScannedFile(
                rel_path=rel,
                abs_path=f,
                workspace_name=workspace_name,
                size=st.st_size,
                mtime=st.st_mtime,
            ))
    return out


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
