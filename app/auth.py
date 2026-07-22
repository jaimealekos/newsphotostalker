"""Autenticación de newsphotostalker.

Usuarios con login/contraseña ("sin más seguridad" por diseño: nada de 2FA,
ni límites de intentos) y sesión mediante cookie firmada con HMAC.

- Las contraseñas se guardan como PBKDF2-SHA256 (nunca en claro).
- La cookie lleva ``user_id.expira.firma``; la firma usa un secreto que se
  autogenera la primera vez en ``data_dir/secret.key``.
- "Recordar sesión": cookie persistente de larga duración. Sin marcar, la
  cookie muere al cerrar el navegador (y el token caduca en horas).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from .config import get_settings

COOKIE_NAME = "ps_session"
PBKDF2_ITERATIONS = 200_000
REMEMBER_DAYS = 365          # con "recordar sesión"
SESSION_HOURS = 24           # sin "recordar sesión" (la cookie además es de sesión)


# --- contraseñas -----------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS)
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _scheme, iters, salt, hexhash = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters))
        return hmac.compare_digest(dk.hex(), hexhash)
    except Exception:
        return False


# --- tokens de sesión ------------------------------------------------------
def _secret() -> bytes:
    path = get_settings().data_dir / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip().encode()


def make_token(user_id: int, *, seconds: int) -> str:
    expires = int(time.time()) + seconds
    payload = f"{user_id}.{expires}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_token(token: str) -> int | None:
    """Devuelve el user_id si el token es válido y no ha caducado."""
    try:
        uid, expires, sig = token.split(".")
        payload = f"{uid}.{expires}"
        good = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            return None
        if int(expires) < time.time():
            return None
        return int(uid)
    except Exception:
        return None
