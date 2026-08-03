"""Login manual de Reuters Connect desde la línea de órdenes.

Abre el navegador con el mismo perfil persistente que usa el adaptador, en la
página de login, y espera a que inicies sesión a mano (email, contraseña y el
desafío de DataDome si aparece). La sesión queda guardada en el perfil, así que
las ejecuciones automáticas posteriores la reutilizan y van sin ventana.

    python -m scripts.login_reuters

La lógica vive en :mod:`app.ingest.reuters_login`, compartida con el botón de
*ajustes*: en la versión empaquetada no hace falta pasar por aquí.
"""

from __future__ import annotations

import os

from app.ingest.reuters_login import DEFAULT_WAIT_MINUTES, STATUS, open_login_window

# En un servidor el login llega por noVNC y puede tardar en atenderse; el helper
# deploy/reuters-login.sh sube la espera con esta variable de entorno.
WAIT_MINUTES = int(os.environ.get("REUTERS_LOGIN_WAIT_MINUTES", str(DEFAULT_WAIT_MINUTES)))


def main() -> int:
    print("Abriendo Chrome en la pagina de login de Reuters...")
    print("Inicia sesion A MANO en la ventana (email, contrasena y CAPTCHA si aparece).")
    mensaje = open_login_window(WAIT_MINUTES)
    print(mensaje)
    return 0 if STATUS.state == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
