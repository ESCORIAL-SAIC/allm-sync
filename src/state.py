"""Estado local en SQLite: mapea cada archivo del share al documento en AnythingLLM.

Es la fuente de verdad del último push exitoso. Permite:
- Detectar altas / modificaciones (por size+mtime, confirmado con sha256).
- Detectar borrados (fila en DB cuyo archivo ya no está en disco).
- Sobrevivir reinicios sin re-subir todo.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileRecord:
    rel_path: str
    workspace_name: str
    workspace_slug: str
    sha256: str
    size: int
    mtime: float
    allm_doc_location: str


class State:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                rel_path           TEXT PRIMARY KEY,
                workspace_name     TEXT NOT NULL,
                workspace_slug     TEXT NOT NULL,
                sha256             TEXT NOT NULL,
                size               INTEGER NOT NULL,
                mtime              REAL NOT NULL,
                allm_doc_location  TEXT NOT NULL,
                updated_at         REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workspaces (
                name        TEXT PRIMARY KEY,
                slug        TEXT NOT NULL,
                doc_folder  TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    # ── workspaces ────────────────────────────────────────────────────────
    def get_workspace(self, name: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM workspaces WHERE name = ?", (name,)
        ).fetchone()

    def upsert_workspace(self, name: str, slug: str, doc_folder: str) -> None:
        self.conn.execute(
            "INSERT INTO workspaces(name, slug, doc_folder) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET slug=excluded.slug, doc_folder=excluded.doc_folder",
            (name, slug, doc_folder),
        )
        self.conn.commit()

    # ── files ─────────────────────────────────────────────────────────────
    def get_file(self, rel_path: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM files WHERE rel_path = ?", (rel_path,)
        ).fetchone()

    def all_file_paths(self) -> set[str]:
        rows = self.conn.execute("SELECT rel_path FROM files").fetchall()
        return {r["rel_path"] for r in rows}

    def upsert_file(self, rec: FileRecord) -> None:
        self.conn.execute(
            "INSERT INTO files(rel_path, workspace_name, workspace_slug, sha256, size, "
            "mtime, allm_doc_location, updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(rel_path) DO UPDATE SET "
            "workspace_name=excluded.workspace_name, workspace_slug=excluded.workspace_slug, "
            "sha256=excluded.sha256, size=excluded.size, mtime=excluded.mtime, "
            "allm_doc_location=excluded.allm_doc_location, updated_at=excluded.updated_at",
            (
                rec.rel_path, rec.workspace_name, rec.workspace_slug, rec.sha256,
                rec.size, rec.mtime, rec.allm_doc_location, time.time(),
            ),
        )
        self.conn.commit()

    def delete_file(self, rel_path: str) -> None:
        self.conn.execute("DELETE FROM files WHERE rel_path = ?", (rel_path,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
