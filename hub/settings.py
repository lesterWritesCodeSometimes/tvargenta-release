# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta — Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# © 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribución y consulta previa. Se distribuye TAL CUAL, sin garantías.
# Ver LICENSE para términos completos.

"""
Settings for the TVArgenta Hub (runs on the hub machine, e.g. pixie).

All persistent hub data lives under HUB_DATA_DIR (default /srv/tvargenta-hub),
separate from the git checkout, so deploys are just `git pull` + restart.
See docs/HUB_DESIGN.md for the layout.
"""

from pathlib import Path
import os

HUB_DATA_DIR = Path(os.environ.get("TVARGENTA_HUB_DATA", "/srv/tvargenta-hub"))

LIBRARY_DIR   = HUB_DATA_DIR / "library"
VIDEO_DIR     = LIBRARY_DIR / "videos"
THUMB_DIR     = LIBRARY_DIR / "thumbnails"
DB_DIR        = HUB_DATA_DIR / "db"
DB_FILE       = DB_DIR / "hub.sqlite"
CHANNELS_DIR  = HUB_DATA_DIR / "channels"
CHANNELS_FILE = CHANNELS_DIR / "channels.json"
METADATA_FILE = HUB_DATA_DIR / "metadata.json"
DEVICES_DIR   = HUB_DATA_DIR / "devices"
INCOMING_DIR  = HUB_DATA_DIR / "incoming"
LOG_DIR       = HUB_DATA_DIR / "logs"
SECRET_FILE   = DB_DIR / "secret_key"

HOST = os.environ.get("TVARGENTA_HUB_HOST", "0.0.0.0")
PORT = int(os.environ.get("TVARGENTA_HUB_PORT", "8090"))

# Rutas relativas permitidas dentro de library/ para upload/download
CONTENT_ROOTS = ("videos", "thumbnails")

# Retención de snapshots de estado por dispositivo:
# se guardan todos los de los últimos RETAIN_DAYS días, y de los anteriores
# solo el último de cada semana ISO.
STATE_RETAIN_DAYS = 30


def ensure_dirs():
    for d in (LIBRARY_DIR, VIDEO_DIR, THUMB_DIR, DB_DIR, CHANNELS_DIR,
              DEVICES_DIR, INCOMING_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
