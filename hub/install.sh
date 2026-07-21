#!/bin/bash
# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta — Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# © 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribución y consulta previa. Se distribuye TAL CUAL, sin garantías.
# Ver LICENSE para términos completos.
#
# Instala el TVArgenta Hub. Correr EN la máquina hub (pixie), desde el
# checkout del repo:
#
#   git clone https://github.com/lesterWritesCodeSometimes/tvargenta-release.git
#   cd tvargenta-release
#   sudo ./hub/install.sh
#
# Después:
#   /srv/tvargenta-hub/venv/bin/python hub/manage.py set-password
#   (y hub/seed_from_mac.sh desde la Mac para poblar la biblioteca)
#
# Deploy de actualizaciones: git pull && sudo systemctl restart tvargenta-hub
set -euo pipefail

HUB_DATA="${TVARGENTA_HUB_DATA:-/srv/tvargenta-hub}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(whoami)}"

if [[ $EUID -ne 0 ]]; then
    echo "Correr con sudo: sudo $0" >&2
    exit 1
fi

echo "== Directorios de datos en $HUB_DATA =="
mkdir -p "$HUB_DATA"/{library/videos,library/thumbnails,db,channels,devices,incoming,logs}
chown -R "$RUN_USER" "$HUB_DATA"

echo "== venv =="
if [[ ! -x "$HUB_DATA/venv/bin/python" ]]; then
    sudo -u "$RUN_USER" python3 -m venv "$HUB_DATA/venv"
fi
sudo -u "$RUN_USER" "$HUB_DATA/venv/bin/pip" install --quiet -r "$REPO_DIR/hub/requirements.txt"

echo "== Init DB =="
sudo -u "$RUN_USER" env TVARGENTA_HUB_DATA="$HUB_DATA" \
    "$HUB_DATA/venv/bin/python" "$REPO_DIR/hub/manage.py" init

echo "== systemd unit =="
cat > /etc/systemd/system/tvargenta-hub.service <<EOF
[Unit]
Description=TVArgenta Hub (content library + device management)
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$REPO_DIR/hub
Environment=TVARGENTA_HUB_DATA=$HUB_DATA
ExecStart=$HUB_DATA/venv/bin/python $REPO_DIR/hub/app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now tvargenta-hub.service
sleep 1
systemctl status tvargenta-hub.service --no-pager | head -8

echo
echo "Hub corriendo en el puerto ${TVARGENTA_HUB_PORT:-8090}."
echo "Siguiente paso: fijar la contraseña del UI:"
echo "  sudo -u $RUN_USER env TVARGENTA_HUB_DATA=$HUB_DATA $HUB_DATA/venv/bin/python $REPO_DIR/hub/manage.py set-password"
