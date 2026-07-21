#!/bin/bash
# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta — Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# © 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribución y consulta previa. Se distribuye TAL CUAL, sin garantías.
# Ver LICENSE para términos completos.
#
# Seed inicial del hub (fase 0). Correr desde la Mac después de hub/install.sh:
#
#   ./hub/seed_from_mac.sh
#
# Hace:
#   1. En pixie: hardlink-copia el mirror existente (argentv_sync) a library/videos.
#      (cp -al: instantáneo, no duplica disco. Seguro porque tanto el hub como
#      rsync escriben vía temp+rename, nunca in-place.)
#   2. Copia thumbnails + metadata.json + canales.json desde el dispositivo
#      (vía la Mac, son ~13 MB).
#   3. En pixie: importa metadata/canales y corre el rescan (hashea 111G, tarda
#      varios minutos — es normal).
set -euo pipefail

PIXIE="${PIXIE:-pixie}"
DEVICE="${DEVICE:-rs@argentv}"
PIXIE_REPO="${PIXIE_REPO:-tvargenta-release}"          # ruta del checkout en pixie (relativa a \$HOME)
HUB_DATA="${HUB_DATA:-/srv/tvargenta-hub}"
SMB_SRC="${SMB_SRC:-/srv/smb/public/videos/argentv_sync}"

MANAGE="env TVARGENTA_HUB_DATA=$HUB_DATA $HUB_DATA/venv/bin/python \$HOME/$PIXIE_REPO/hub/manage.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "== 1/3 Videos: hardlink del mirror existente en pixie =="
ssh "$PIXIE" "
    set -e
    if [ -e '$HUB_DATA/library/videos/.seeded' ]; then
        echo '   (ya seedeado, salto)'
    else
        cp -al '$SMB_SRC/.' '$HUB_DATA/library/videos/'
        touch '$HUB_DATA/library/videos/.seeded'
        echo \"   \$(find '$HUB_DATA/library/videos' -type f | wc -l) archivos enlazados\"
    fi
"

echo "== 2/3 Thumbnails + metadata + canales desde el dispositivo =="
rsync -a "$DEVICE:/srv/tvargenta/content/thumbnails/" "$TMP/thumbnails/"
scp -q "$DEVICE:/srv/tvargenta/content/metadata.json" "$TMP/"
scp -q "$DEVICE:/srv/tvargenta/content/canales.json" "$TMP/"
rsync -a "$TMP/thumbnails/" "$PIXIE:$HUB_DATA/library/thumbnails/"
scp -q "$TMP/metadata.json" "$TMP/canales.json" "$PIXIE:/tmp/"

echo "== 3/3 Import + rescan en pixie (hashear 111G tarda varios minutos) =="
ssh "$PIXIE" "
    set -e
    $MANAGE import-metadata /tmp/metadata.json
    $MANAGE import-channels /tmp/canales.json
    rm /tmp/metadata.json /tmp/canales.json
    $MANAGE rescan
"

echo
echo "Seed completo. Abrí http://$PIXIE:8090 para ver la biblioteca."
