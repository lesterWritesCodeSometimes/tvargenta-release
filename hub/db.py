# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta — Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# © 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribución y consulta previa. Se distribuye TAL CUAL, sin garantías.
# Ver LICENSE para términos completos.

"""
SQLite state for the hub: content index (with sha256 cache), tokens,
device registry, and monotonic version counters.

Tokens se guardan hasheados (sha256); el token en claro solo se muestra
una vez al crearlo.
"""

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager

from settings import DB_FILE, VIDEO_DIR, THUMB_DIR, LIBRARY_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    path   TEXT PRIMARY KEY,      -- relativo a library/, p.ej. videos/series/X/y.mp4
    size   INTEGER NOT NULL,
    mtime  REAL NOT NULL,
    sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT UNIQUE NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('admin', 'device')),
    device_id  TEXT,              -- solo para role='device'
    label      TEXT NOT NULL,
    created    REAL NOT NULL,
    last_seen  REAL,
    revoked    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS devices (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    created       REAL NOT NULL,
    last_seen     REAL,
    last_snapshot REAL,
    info          TEXT             -- JSON reportado por el agente (disco, versiones)
);
"""

VERSION_KEYS = ("library_version", "channels_version", "metadata_version")


@contextmanager
def get_db():
    con = sqlite3.connect(DB_FILE, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with get_db() as con:
        con.executescript(SCHEMA)
        for key in VERSION_KEYS:
            con.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, '1')", (key,))


def get_version(con, key):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return int(row["value"]) if row else 1


def bump_version(con, key):
    con.execute(
        "UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = ?", (key,)
    )
    return get_version(con, key)


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(con, key, value):
    con.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# --- content index ---------------------------------------------------------

def sha256_file(path, chunk_size=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def rescan_library(progress=None):
    """Reconcilia el índice con el disco. Hashea solo archivos nuevos o
    con size/mtime distinto. Devuelve (added, changed, removed)."""
    on_disk = {}
    for root in (VIDEO_DIR, THUMB_DIR):
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.is_file() and not p.name.startswith("."):
                rel = str(p.relative_to(LIBRARY_DIR))
                st = p.stat()
                on_disk[rel] = (st.st_size, st.st_mtime)

    added = changed = 0
    with get_db() as con:
        indexed = {
            r["path"]: (r["size"], r["mtime"])
            for r in con.execute("SELECT path, size, mtime FROM files")
        }
        stale = [p for p in indexed if p not in on_disk]
        for p in stale:
            con.execute("DELETE FROM files WHERE path = ?", (p,))

        todo = [p for p, sig in on_disk.items() if indexed.get(p) != sig]
        for i, rel in enumerate(sorted(todo)):
            size, mtime = on_disk[rel]
            if progress:
                progress(f"[{i + 1}/{len(todo)}] {rel}")
            digest = sha256_file(LIBRARY_DIR / rel)
            if rel in indexed:
                changed += 1
            else:
                added += 1
            con.execute(
                "INSERT INTO files (path, size, mtime, sha256) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET size=excluded.size, "
                "mtime=excluded.mtime, sha256=excluded.sha256",
                (rel, size, mtime, digest),
            )
        if added or changed or stale:
            bump_version(con, "library_version")
    return added, changed, len(stale)


def upsert_file(con, rel_path, size, mtime, sha256):
    con.execute(
        "INSERT INTO files (path, size, mtime, sha256) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET size=excluded.size, "
        "mtime=excluded.mtime, sha256=excluded.sha256",
        (rel_path, size, mtime, sha256),
    )
    bump_version(con, "library_version")


def remove_file(con, rel_path):
    con.execute("DELETE FROM files WHERE path = ?", (rel_path,))
    bump_version(con, "library_version")


# --- tokens ----------------------------------------------------------------

def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(role, label, device_id=None):
    """Crea un token y devuelve el valor en claro (única vez que existe)."""
    token = f"tvh_{secrets.token_urlsafe(32)}"
    with get_db() as con:
        con.execute(
            "INSERT INTO tokens (token_hash, role, device_id, label, created) "
            "VALUES (?, ?, ?, ?, ?)",
            (hash_token(token), role, device_id, label, time.time()),
        )
        if role == "device" and device_id:
            con.execute(
                "INSERT INTO devices (id, name, created) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (device_id, device_id, time.time()),
            )
    return token


def lookup_token(token):
    """Devuelve la fila del token (y actualiza last_seen) o None."""
    with get_db() as con:
        row = con.execute(
            "SELECT * FROM tokens WHERE token_hash = ? AND revoked = 0",
            (hash_token(token),),
        ).fetchone()
        if row:
            now = time.time()
            con.execute("UPDATE tokens SET last_seen = ? WHERE id = ?", (now, row["id"]))
            if row["device_id"]:
                con.execute(
                    "UPDATE devices SET last_seen = ? WHERE id = ?",
                    (now, row["device_id"]),
                )
        return dict(row) if row else None


def revoke_token(token_id):
    with get_db() as con:
        con.execute("UPDATE tokens SET revoked = 1 WHERE id = ?", (token_id,))


def list_tokens():
    with get_db() as con:
        return [dict(r) for r in con.execute(
            "SELECT id, role, device_id, label, created, last_seen, revoked "
            "FROM tokens ORDER BY created DESC"
        )]


# --- devices ---------------------------------------------------------------

def list_devices():
    with get_db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM devices ORDER BY id")]


def get_device(device_id):
    with get_db() as con:
        row = con.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        return dict(row) if row else None


def record_snapshot(device_id, info=None):
    with get_db() as con:
        con.execute(
            "UPDATE devices SET last_snapshot = ?, info = ? WHERE id = ?",
            (time.time(), json.dumps(info) if info else None, device_id),
        )
