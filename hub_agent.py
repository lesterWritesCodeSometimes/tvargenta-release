# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta — Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# © 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribución y consulta previa. Se distribuye TAL CUAL, sin garantías.
# Ver LICENSE para términos completos.

"""
Hub agent (device side) — phase 1: pushes a state snapshot to the hub.

Runs as a systemd oneshot (see install_hub_agent.sh) on a timer. The hub
deduplicates identical snapshots, so running hourly is cheap.

Config: content/hub_agent.json
    {"hub_url": "http://pixie:8090", "token": "tvh_...", "device_id": "argentv"}

Content pull + channel apply (phase 2) will live here too; for now this
only backs up device-owned state.
"""

import json
import shutil
import sys
import urllib.error
import urllib.request

from settings import CONTENT_DIR

AGENT_CONFIG_FILE = CONTENT_DIR / "hub_agent.json"

# Estado del dispositivo que respaldamos. Explícito a propósito: nada de
# barrer *.json (evita subir tokens o archivos derivados grandes).
STATE_FILES = [
    "tapes.json",
    "plays.json",
    "episode_cursors.json",
    "configuracion.json",
    "menu_configuracion.json",
    "volumen.json",
    "series.json",
    "tags.json",
    "canales.json",
    "canal_activo.json",
    "channel_state.json",
    "weekly_schedule.json",
    "wifi_known.json",
    "metadata.json",
    "channel_detection_cache.json",
]


def load_config():
    try:
        with open(AGENT_CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        sys.exit(f"[HUB-AGENT] Falta {AGENT_CONFIG_FILE} — ver install_hub_agent.sh")
    for key in ("hub_url", "token", "device_id"):
        if not cfg.get(key):
            sys.exit(f"[HUB-AGENT] Falta '{key}' en {AGENT_CONFIG_FILE}")
    return cfg


def collect_state():
    files = {}
    for name in STATE_FILES:
        path = CONTENT_DIR / name
        if not path.exists():
            continue
        try:
            with open(path, encoding="utf-8") as f:
                files[name] = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[HUB-AGENT] Omitiendo {name}: {e}")
    return files


def push_state(cfg):
    files = collect_state()
    if not files:
        sys.exit("[HUB-AGENT] No hay archivos de estado que subir")
    disk = shutil.disk_usage(CONTENT_DIR)
    body = json.dumps({
        "files": files,
        "info": {"disk": {"total": disk.total, "used": disk.used}},
    }).encode()

    url = f"{cfg['hub_url'].rstrip('/')}/api/v1/devices/{cfg['device_id']}/state"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {cfg['token']}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"[HUB-AGENT] Hub respondió {e.code}: {e.read().decode(errors='replace')[:200]}")
    except urllib.error.URLError as e:
        sys.exit(f"[HUB-AGENT] No se pudo contactar al hub: {e.reason}")
    print(f"[HUB-AGENT] Snapshot {result.get('status')} ({len(files)} archivos, "
          f"snapshot={result.get('snapshot')})")


if __name__ == "__main__":
    push_state(load_config())
