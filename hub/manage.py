# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta — Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# © 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribución y consulta previa. Se distribuye TAL CUAL, sin garantías.
# Ver LICENSE para términos completos.

"""
Admin CLI for the hub. Run on the hub machine from the repo's hub/ dir:

    python manage.py init                    # crea dirs + esquema
    python manage.py set-password            # contraseña del UI (prompt)
    python manage.py create-token --role admin --label cli
    python manage.py create-token --role device --label livingroom --device-id argentv
    python manage.py list-tokens
    python manage.py revoke-token <id>
    python manage.py rescan                  # indexa library/ (hashea nuevos/cambiados)
    python manage.py import-channels <file>  # canales.json inicial -> lineup global
    python manage.py import-metadata <file>  # metadata.json inicial
"""

import argparse
import getpass
import json
import shutil
import sys
from datetime import datetime, UTC

from werkzeug.security import generate_password_hash

import db
from settings import CHANNELS_FILE, CHANNELS_DIR, METADATA_FILE, ensure_dirs


def cmd_init(_args):
    ensure_dirs()
    db.init_db()
    print("Hub data dirs + DB listos.")


def cmd_set_password(_args):
    pw = getpass.getpass("Nueva contraseña admin: ")
    if len(pw) < 8:
        sys.exit("Mínimo 8 caracteres.")
    if getpass.getpass("Repetir: ") != pw:
        sys.exit("No coinciden.")
    with db.get_db() as con:
        db.set_meta(con, "admin_password_hash", generate_password_hash(pw))
    print("Contraseña actualizada.")


def cmd_create_token(args):
    if args.role == "device" and not args.device_id:
        sys.exit("--device-id es obligatorio para role=device")
    token = db.create_token(args.role, args.label, args.device_id)
    print("Token (guárdalo ahora, no se vuelve a mostrar):")
    print(f"  {token}")


def cmd_list_tokens(_args):
    for t in db.list_tokens():
        status = "REVOKED" if t["revoked"] else "active"
        seen = datetime.fromtimestamp(t["last_seen"], UTC).isoformat() if t["last_seen"] else "never"
        print(f"[{t['id']}] {t['role']:<6} {t['label']:<20} device={t['device_id'] or '-':<12} "
              f"{status:<8} last_seen={seen}")


def cmd_revoke_token(args):
    db.revoke_token(args.token_id)
    print(f"Token {args.token_id} revocado.")


def cmd_rescan(_args):
    added, changed, removed = db.rescan_library(progress=lambda msg: print(msg, flush=True))
    print(f"Rescan: +{added} nuevos, ~{changed} cambiados, -{removed} eliminados.")


def cmd_import_channels(args):
    with open(args.file, encoding="utf-8") as f:
        data = json.load(f)
    if CHANNELS_FILE.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(CHANNELS_FILE, CHANNELS_DIR / f"channels.{stamp}.json")
    with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    with db.get_db() as con:
        version = db.bump_version(con, "channels_version")
    print(f"Lineup importado ({len(data)} canales), channels_version={version}.")


def cmd_import_metadata(args):
    with open(args.file, encoding="utf-8") as f:
        data = json.load(f)
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    with db.get_db() as con:
        version = db.bump_version(con, "metadata_version")
    print(f"Metadata importada ({len(data)} títulos), metadata_version={version}.")


def main():
    parser = argparse.ArgumentParser(description="TVArgenta Hub admin CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)
    sub.add_parser("set-password").set_defaults(fn=cmd_set_password)

    p = sub.add_parser("create-token")
    p.add_argument("--role", choices=("admin", "device"), required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--device-id")
    p.set_defaults(fn=cmd_create_token)

    sub.add_parser("list-tokens").set_defaults(fn=cmd_list_tokens)

    p = sub.add_parser("revoke-token")
    p.add_argument("token_id", type=int)
    p.set_defaults(fn=cmd_revoke_token)

    sub.add_parser("rescan").set_defaults(fn=cmd_rescan)

    p = sub.add_parser("import-channels")
    p.add_argument("file")
    p.set_defaults(fn=cmd_import_channels)

    p = sub.add_parser("import-metadata")
    p.add_argument("file")
    p.set_defaults(fn=cmd_import_metadata)

    args = parser.parse_args()
    ensure_dirs()
    db.init_db()
    args.fn(args)


if __name__ == "__main__":
    main()
