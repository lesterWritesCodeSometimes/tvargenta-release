# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta — Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# © 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribución y consulta previa. Se distribuye TAL CUAL, sin garantías.
# Ver LICENSE para términos completos.

"""
TVArgenta Hub — central content library and device management.

API (Bearer token) + web UI (session login). See docs/HUB_DESIGN.md.
The web UI is deliberately read-only for content: ingestion happens via
`PUT /api/v1/content/<path>` from the encode CLI.
"""

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import time
from datetime import datetime, timedelta, UTC
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import (
    Flask, request, session, redirect, url_for, jsonify, render_template,
    send_file, flash, abort, g,
)
from werkzeug.security import check_password_hash

import db
from auth import require_token, require_login
from settings import (
    LIBRARY_DIR, VIDEO_DIR, THUMB_DIR, CHANNELS_DIR, CHANNELS_FILE,
    METADATA_FILE, DEVICES_DIR, INCOMING_DIR, LOG_DIR, SECRET_FILE,
    CONTENT_ROOTS, STATE_RETAIN_DAYS, HOST, PORT, ensure_dirs,
)

ensure_dirs()
db.init_db()

app = Flask(__name__)

if SECRET_FILE.exists():
    app.secret_key = SECRET_FILE.read_text().strip()
else:
    app.secret_key = secrets.token_hex(32)
    SECRET_FILE.write_text(app.secret_key)
    SECRET_FILE.chmod(0o600)

logger = logging.getLogger("tvargenta-hub")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = RotatingFileHandler(LOG_DIR / "hub.log", maxBytes=3_000_000, backupCount=5)
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                             datefmt="%Y-%m-%d %H:%M:%S")
    _h.setFormatter(_fmt)
    logger.addHandler(_h)
    _sh = logging.StreamHandler()
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)


# --- helpers ---------------------------------------------------------------

def safe_library_path(rel_path):
    """Valida una ruta relativa de contenido. Devuelve Path absoluto o None."""
    if rel_path != rel_path.strip() or "\x00" in rel_path:
        return None
    parts = Path(rel_path).parts
    if not parts or parts[0] not in CONTENT_ROOTS:
        return None
    if any(p in ("..", "") or p.startswith(".") for p in parts):
        return None
    abs_path = (LIBRARY_DIR / rel_path).resolve()
    if not str(abs_path).startswith(str(LIBRARY_DIR.resolve()) + os.sep):
        return None
    return abs_path


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json_atomic(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def manifest_etag(con):
    return "lib{}-ch{}-md{}".format(
        db.get_version(con, "library_version"),
        db.get_version(con, "channels_version"),
        db.get_version(con, "metadata_version"),
    )


SNAPSHOT_TS_RE = re.compile(r"^\d{8}T\d{6}Z$")
SNAPSHOT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.json$")


def device_snapshots(device_id):
    state_dir = DEVICES_DIR / device_id / "state"
    if not state_dir.exists():
        return []
    return sorted(
        (d for d in state_dir.iterdir() if d.is_dir() and SNAPSHOT_TS_RE.match(d.name)),
        key=lambda d: d.name, reverse=True,
    )


def prune_snapshots(device_id):
    """Mantiene todos los snapshots de los últimos STATE_RETAIN_DAYS días;
    de los más viejos, solo el último de cada semana ISO."""
    cutoff = (datetime.now(UTC) - timedelta(days=STATE_RETAIN_DAYS)).strftime("%Y%m%dT%H%M%SZ")
    keep_per_week = {}
    for snap in device_snapshots(device_id):     # newest first
        if snap.name >= cutoff:
            continue
        week = datetime.strptime(snap.name, "%Y%m%dT%H%M%SZ").isocalendar()[:2]
        if week in keep_per_week:
            shutil.rmtree(snap)
        else:
            keep_per_week[week] = snap


# --- API: manifest y contenido ---------------------------------------------

@app.route("/api/v1/manifest")
@require_token("admin", "device")
def api_manifest():
    with db.get_db() as con:
        etag = manifest_etag(con)
        if request.if_none_match.contains(etag):
            return "", 304, {"ETag": f'"{etag}"'}
        files = [
            {"path": r["path"], "size": r["size"], "sha256": r["sha256"]}
            for r in con.execute("SELECT path, size, sha256 FROM files ORDER BY path")
        ]
        body = {
            "library_version": db.get_version(con, "library_version"),
            "channels_version": db.get_version(con, "channels_version"),
            "metadata_version": db.get_version(con, "metadata_version"),
            "files": files,
        }
    return jsonify(body), 200, {"ETag": f'"{etag}"'}


@app.route("/api/v1/content/<path:rel_path>")
@require_token("admin", "device")
def api_content_get(rel_path):
    abs_path = safe_library_path(rel_path)
    if not abs_path or not abs_path.is_file():
        return jsonify({"error": "not found"}), 404
    return send_file(abs_path, conditional=True)


@app.route("/api/v1/content/<path:rel_path>", methods=["PUT"])
@require_token("admin")
def api_content_put(rel_path):
    abs_path = safe_library_path(rel_path)
    if not abs_path:
        return jsonify({"error": "invalid path"}), 400
    expected = request.headers.get("X-Content-SHA256", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return jsonify({"error": "missing or malformed X-Content-SHA256"}), 400

    rel = str(abs_path.relative_to(LIBRARY_DIR))
    tmp = INCOMING_DIR / f"{expected}.part"
    h = hashlib.sha256()
    size = 0
    with open(tmp, "wb") as f:
        while chunk := request.stream.read(1024 * 1024):
            h.update(chunk)
            size += len(chunk)
            f.write(chunk)
        f.flush()
        os.fsync(f.fileno())

    if h.hexdigest() != expected:
        tmp.unlink(missing_ok=True)
        return jsonify({"error": "sha256 mismatch", "received": h.hexdigest()}), 422

    with db.get_db() as con:
        row = con.execute("SELECT sha256 FROM files WHERE path = ?", (rel,)).fetchone()
        if row and row["sha256"] == expected and abs_path.is_file():
            tmp.unlink(missing_ok=True)
            return jsonify({"status": "unchanged", "path": rel, "sha256": expected})
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, abs_path)
        db.upsert_file(con, rel, size, abs_path.stat().st_mtime, expected)
    logger.info(f"[UPLOAD] {rel} ({size} bytes) por token '{g.token['label']}'")
    return jsonify({"status": "stored", "path": rel, "size": size, "sha256": expected}), 201


@app.route("/api/v1/content/<path:rel_path>", methods=["DELETE"])
@require_token("admin")
def api_content_delete(rel_path):
    abs_path = safe_library_path(rel_path)
    if not abs_path or not abs_path.is_file():
        return jsonify({"error": "not found"}), 404
    rel = str(abs_path.relative_to(LIBRARY_DIR))
    abs_path.unlink()
    with db.get_db() as con:
        db.remove_file(con, rel)
    logger.info(f"[DELETE] {rel} por token '{g.token['label']}'")
    return jsonify({"status": "deleted", "path": rel})


# --- API: channels y metadata ----------------------------------------------

@app.route("/api/v1/channels")
@require_token("admin", "device")
def api_channels_get():
    with db.get_db() as con:
        version = db.get_version(con, "channels_version")
    return jsonify({"version": version, "channels": load_json(CHANNELS_FILE, {})})


@app.route("/api/v1/channels", methods=["PUT"])
@require_token("admin")
def api_channels_put():
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "body must be a JSON object"}), 400
    if CHANNELS_FILE.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(CHANNELS_FILE, CHANNELS_DIR / f"channels.{stamp}.json")
    write_json_atomic(CHANNELS_FILE, data)
    with db.get_db() as con:
        version = db.bump_version(con, "channels_version")
    logger.info(f"[CHANNELS] lineup actualizado a v{version} por '{g.token['label']}'")
    return jsonify({"status": "stored", "version": version})


@app.route("/api/v1/metadata")
@require_token("admin", "device")
def api_metadata_get():
    with db.get_db() as con:
        version = db.get_version(con, "metadata_version")
    return jsonify({"version": version, "metadata": load_json(METADATA_FILE, {})})


@app.route("/api/v1/metadata", methods=["PUT"])
@require_token("admin")
def api_metadata_put():
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "body must be a JSON object"}), 400
    write_json_atomic(METADATA_FILE, data)
    with db.get_db() as con:
        version = db.bump_version(con, "metadata_version")
    return jsonify({"status": "stored", "version": version})


# --- API: devices -----------------------------------------------------------

@app.route("/api/v1/devices")
@require_token("admin")
def api_devices():
    devices = db.list_devices()
    for d in devices:
        d["info"] = json.loads(d["info"]) if d["info"] else None
        d["snapshots"] = len(device_snapshots(d["id"]))
    return jsonify({"devices": devices})


@app.route("/api/v1/devices/<device_id>/state", methods=["POST"])
@require_token("admin", "device")
def api_device_state(device_id):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", device_id):
        return jsonify({"error": "invalid device id"}), 400
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("files"), dict):
        return jsonify({"error": "body must be {files: {...}, info?: {...}}"}), 400
    files = body["files"]
    if not all(SNAPSHOT_NAME_RE.match(name) for name in files):
        return jsonify({"error": "snapshot file names must be simple *.json names"}), 400

    digest = hashlib.sha256(
        json.dumps(files, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    snapshots = device_snapshots(device_id)
    if snapshots:
        last_digest_file = snapshots[0] / ".digest"
        if last_digest_file.exists() and last_digest_file.read_text().strip() == digest:
            db.record_snapshot(device_id, body.get("info"))
            return jsonify({"status": "unchanged", "snapshot": snapshots[0].name})

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    snap_dir = DEVICES_DIR / device_id / "state" / stamp
    snap_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        write_json_atomic(snap_dir / name, content)
    (snap_dir / ".digest").write_text(digest)
    db.record_snapshot(device_id, body.get("info"))
    prune_snapshots(device_id)
    logger.info(f"[STATE] snapshot {stamp} de '{device_id}' ({len(files)} archivos)")
    return jsonify({"status": "stored", "snapshot": stamp}), 201


# --- UI ---------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        with db.get_db() as con:
            pw_hash = db.get_meta(con, "admin_password_hash")
        if pw_hash and check_password_hash(pw_hash, request.form.get("password", "")):
            session["logged_in"] = True
            session.permanent = True
            target = request.args.get("next") or url_for("library")
            if not target.startswith("/"):
                target = url_for("library")
            return redirect(target)
        error = "Contraseña incorrecta"
        time.sleep(1)  # frena fuerza bruta casera
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@require_login
def library():
    metadata = load_json(METADATA_FILE, {})
    query = request.args.get("q", "").strip().lower()
    category = request.args.get("category", "")

    thumbs = {p.stem: p.name for p in THUMB_DIR.iterdir()} if THUMB_DIR.exists() else {}
    items = []
    with db.get_db() as con:
        rows = con.execute(
            "SELECT path, size FROM files WHERE path LIKE 'videos/%' ORDER BY path"
        ).fetchall()
    for r in rows:
        stem = Path(r["path"]).stem
        meta = metadata.get(stem, {})
        item = {
            "stem": stem,
            "path": r["path"],
            "size": r["size"],
            "title": meta.get("title", stem),
            "category": meta.get("category", ""),
            "series": meta.get("series", ""),
            "thumb": thumbs.get(stem),
        }
        if query and query not in item["title"].lower() and query not in stem.lower():
            continue
        if category and item["category"] != category:
            continue
        items.append(item)

    categories = sorted({m.get("category", "") for m in metadata.values()} - {""})
    total_bytes = sum(i["size"] for i in items)
    return render_template("library.html", items=items, categories=categories,
                           query=query, category=category, total_bytes=total_bytes,
                           active_page="library")


@app.route("/thumb/<path:filename>")
@require_login
def thumb(filename):
    abs_path = safe_library_path(f"thumbnails/{filename}")
    if not abs_path or not abs_path.is_file():
        abort(404)
    return send_file(abs_path, conditional=True)


@app.route("/channels")
@require_login
def channels():
    with db.get_db() as con:
        version = db.get_version(con, "channels_version")
    lineup = load_json(CHANNELS_FILE, {})
    ordered = sorted(lineup.items(),
                     key=lambda kv: (not kv[0].isdigit(), int(kv[0]) if kv[0].isdigit() else kv[0]))
    return render_template("channels.html", channels=ordered,
                           version=version, active_page="channels")


@app.route("/devices")
@require_login
def devices():
    devs = db.list_devices()
    for d in devs:
        d["info"] = json.loads(d["info"]) if d["info"] else None
        d["snapshots"] = len(device_snapshots(d["id"]))
    return render_template("devices.html", devices=devs, active_page="devices")


@app.route("/devices/<device_id>")
@require_login
def device_detail(device_id):
    dev = db.get_device(device_id)
    if not dev:
        abort(404)
    dev["info"] = json.loads(dev["info"]) if dev["info"] else None
    snapshots = [
        {"name": s.name,
         "files": sorted(f.name for f in s.iterdir() if f.suffix == ".json")}
        for s in device_snapshots(device_id)
    ]
    return render_template("device_detail.html", device=dev, snapshots=snapshots,
                           active_page="devices")


@app.route("/devices/<device_id>/state/<snapshot>/<name>")
@require_login
def device_state_file(device_id, snapshot, name):
    if not (SNAPSHOT_TS_RE.match(snapshot) and SNAPSHOT_NAME_RE.match(name)
            and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", device_id)):
        abort(404)
    path = DEVICES_DIR / device_id / "state" / snapshot / name
    if not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True)


@app.route("/tokens", methods=["GET", "POST"])
@require_login
def tokens():
    new_token = None
    if request.method == "POST":
        role = request.form.get("role", "")
        label = request.form.get("label", "").strip()
        device_id = request.form.get("device_id", "").strip() or None
        if role not in ("admin", "device") or not label:
            flash("Role y label son obligatorios")
        elif role == "device" and not (device_id and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", device_id)):
            flash("Un token de device necesita un device_id ([a-z0-9_-])")
        else:
            new_token = db.create_token(role, label, device_id)
    return render_template("tokens.html", tokens=db.list_tokens(),
                           new_token=new_token, active_page="tokens")


@app.route("/tokens/<int:token_id>/revoke", methods=["POST"])
@require_login
def token_revoke(token_id):
    db.revoke_token(token_id)
    return redirect(url_for("tokens"))


@app.template_filter("human_bytes")
def human_bytes(n):
    n = n or 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


@app.template_filter("human_ts")
def human_ts(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC")


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
