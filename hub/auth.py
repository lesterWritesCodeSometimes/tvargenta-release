# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta — Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# © 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribución y consulta previa. Se distribuye TAL CUAL, sin garantías.
# Ver LICENSE para términos completos.

"""
Auth for the hub: bearer tokens for the API, session login for the UI.

- require_token(*roles): decorator para rutas API. Deja el token en g.token.
  Para role='device', las rutas con <device_id> exigen que coincida con el
  device_id del token (un dispositivo solo escribe su propio estado).
- require_login: decorator para rutas UI (sesión Flask).
"""

from functools import wraps

from flask import g, request, session, redirect, url_for, jsonify

import db


def _bearer_token():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    return None


def require_token(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            token_value = _bearer_token()
            token = db.lookup_token(token_value) if token_value else None
            if not token or token["role"] not in roles:
                return jsonify({"error": "unauthorized"}), 401
            if token["role"] == "device" and "device_id" in kwargs:
                if token["device_id"] != kwargs["device_id"]:
                    return jsonify({"error": "forbidden"}), 403
            g.token = token
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper
