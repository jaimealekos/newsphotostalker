"""Login manual de Reuters Connect desde la línea de órdenes.

Abre el navegador normal del sistema (Chrome/Edge, sin automatizar) en la página
de login de Reuters, apuntando al perfil persistente de la app. Inicia sesión a
mano —email, contraseña y el CAPTCHA de DataDome si aparece— hasta ver tu panel de
Reuters, vuelve a esta terminal y pulsa Intro para que se compruebe la sesión.

    python -m scripts.login_reuters

La lógica vive en :mod:`app.ingest.reuters_login`, la misma que usa el botón de
*ajustes*: en la versión de escritorio se maneja todo desde el panel.
"""

from __future__ import annotations

from app.ingest.reuters_login import STATUS, finish_login, start_login


def main() -> int:
    print(start_login())
    if STATUS.state != "open":
        return 1  # no se pudo abrir el navegador (ya lo dijo start_login)
    try:
        input("\nCuando hayas entrado en Reuters y veas tu panel, pulsa Intro aquí… ")
    except (EOFError, KeyboardInterrupt):
        print()
    print(finish_login())
    return 0 if STATUS.state == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
