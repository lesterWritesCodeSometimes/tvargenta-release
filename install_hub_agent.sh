#!/bin/bash
# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta — Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# © 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribución y consulta previa. Se distribuye TAL CUAL, sin garantías.
# Ver LICENSE para términos completos.
#
# Instala el hub agent (respaldo de estado hacia el hub) en el dispositivo.
# Correr EN el dispositivo, desde el checkout del repo (p.ej. /srv/tvargenta):
#
#   1. Crear un token de device en el hub (UI > Tokens, o manage.py create-token)
#   2. Crear content/hub_agent.json:
#        {"hub_url": "http://pixie:8090", "token": "tvh_...", "device_id": "argentv"}
#   3. sudo ./install_hub_agent.sh
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="${SUDO_USER:-$(whoami)}"
CONFIG_FILE="$REPO_DIR/content/hub_agent.json"

if [[ $EUID -ne 0 ]]; then
    echo "Correr con sudo: sudo $0" >&2
    exit 1
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Falta $CONFIG_FILE — crearlo primero (ver comentario arriba)." >&2
    exit 1
fi
chmod 600 "$CONFIG_FILE"
chown "$RUN_USER" "$CONFIG_FILE"

PYTHON="$REPO_DIR/venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"

cat > /etc/systemd/system/tvargenta-hub-agent.service <<EOF
[Unit]
Description=TVArgenta hub agent (state snapshot push)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$REPO_DIR
ExecStart=$PYTHON $REPO_DIR/hub_agent.py
EOF

cat > /etc/systemd/system/tvargenta-hub-agent.timer <<EOF
[Unit]
Description=TVArgenta hub agent hourly state push

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now tvargenta-hub-agent.timer

echo "Timer instalado. Primer push de prueba:"
systemctl start tvargenta-hub-agent.service
systemctl status tvargenta-hub-agent.service --no-pager -l | tail -5
