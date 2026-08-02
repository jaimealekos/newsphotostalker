"""Login manual de Reuters Connect.

Abre el navegador headed con el MISMO perfil persistente y la misma huella que
usa el adaptador de Reuters, en la página de login, y espera a que el usuario
inicie sesión a mano (email, contraseña y el desafío de DataDome si aparece).
La sesión queda guardada en el perfil, así que las ejecuciones automáticas
posteriores la reutilizan y no vuelven a pasar por el login.

Úsalo cuando el login automático del adaptador se quede atascado en el
CAPTCHA del bot-wall:

    python -m scripts.login_reuters
"""

from __future__ import annotations

import os
import time

from app.config import get_settings
from app.ingest.reuters import ReutersAdapter

# En el NAS el login llega por noVNC y puede tardar en atenderse; el helper
# deploy/reuters-login.sh sube la espera con esta variable de entorno.
WAIT_MINUTES = int(os.environ.get("REUTERS_LOGIN_WAIT_MINUTES", "9"))


def main() -> int:
    settings = get_settings()
    adapter = ReutersAdapter(settings, settings.credentials_for("reuters"))
    # Sombra de instancia: open() no debe lanzar el login automático.
    adapter.requires_login = False
    # Aquí SÍ hace falta ver la ventana: el login lo hace una persona. En las
    # ejecuciones normales el navegador va sin ventana (ver LiveAdapter).
    adapter.show_window = True
    adapter.open()
    page = adapter.page

    try:
        page.goto("https://www.reutersconnect.com/all", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        if adapter._looks_logged_in():
            print("Ya hay sesion guardada en el perfil; no hace falta login.")
            return 0

        page.goto("https://www.reutersconnect.com/login", wait_until="domcontentloaded")
        print("Inicia sesion A MANO en la ventana de Chrome que se ha abierto")
        print("(email, contrasena y CAPTCHA si aparece).")
        print(f"Esperando hasta {WAIT_MINUTES} minutos a que se complete...")

        deadline = time.time() + WAIT_MINUTES * 60
        while time.time() < deadline:
            time.sleep(3)
            try:
                pages = adapter._context.pages
            except Exception:
                print("El navegador se cerro del todo; no se pudo comprobar el login.")
                break
            if not pages:
                print("Se cerraron todas las pestanas antes de detectar el login.")
                break
            urls = []
            for pg in pages:
                try:
                    urls.append(pg.url)
                except Exception:
                    continue
            if any(
                "reutersconnect.com" in u
                and "/login" not in u
                and "auth.thomsonreuters.com" not in u
                for u in urls
            ):
                # Deja que la pagina asiente antes de cerrar el perfil.
                time.sleep(4)
                print("Sesion iniciada y guardada en el perfil.")
                print("Las proximas ejecuciones de Reuters no pediran login.")
                return 0
        print("No se detecto el login. Vuelve a ejecutar el script para reintentar.")
        return 1
    finally:
        try:
            adapter.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
