"""Mantiene viva la sesión de Reuters para no repetir el login humano.

Reuters Connect exige login humano UNA vez (el slider de DataDome no se
automatiza). Para que esa sesión no caduque, este keep-alive carga
periódicamente la home logueada: eso renueva la cookie ``datadome`` y dispara
la renovación silenciosa del token auth0. Mientras la sesión siga viva, las
ejecuciones normales pasan el bot-wall sin re-login.

Si la parte de auth cae pero el dispositivo sigue siendo de confianza para
DataDome (cookie datadome viva), se intenta un **re-login automático**
(email+password); DataDome no muestra el slider a un dispositivo ya confiable,
así que suele completarse sin humano. NO se intenta resolver ningún CAPTCHA:
si aparece el slider, el login falla y se dispara la alerta por email para que
un humano rehaga el login.

El intervalo lo fija ``reuters_keepalive_minutes`` (config). Se serializa con
las búsquedas mediante el lock del runner (una sola instancia de navegador
sobre el perfil a la vez).
"""

from __future__ import annotations

import logging

from .. import alerts
from ..config import get_settings
from .runner import _RUN_LOCK

log = logging.getLogger("keepalive")

WARM_URL = "https://www.reutersconnect.com/all"


def keepalive_reuters() -> None:
    settings = get_settings()
    cred = settings.credentials_for("reuters")
    if not cred.enabled or not cred.has_login:
        return

    from .live_base import LiveAdapterError
    from .reuters import ReutersAdapter

    with _RUN_LOCK:
        adapter = ReutersAdapter(settings, cred)
        adapter.requires_login = False  # open() no debe forzar el login
        try:
            adapter.open()
            page = adapter.page
            page.goto(WARM_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            if adapter._looks_logged_in():
                try:
                    adapter.autoscroll(rounds=1)  # navegación ligera: refresca cookies
                except Exception:  # noqa: BLE001
                    pass
                alerts.record_run(settings, "reuters", ok=True)
                log.info("keepalive: sesión Reuters viva")
                return

            # Sesión de auth caída. Reintentamos re-login automático SOLO si no
            # estábamos ya en fallo (para no martillear el slider de DataDome).
            if alerts.status(settings, "reuters") == "failing":
                log.warning("keepalive: sesión caída y ya avisado; sin reintento")
                return

            log.warning("keepalive: sesión caída, intento re-login automático")
            adapter.login()
            if adapter._looks_logged_in():
                alerts.record_run(settings, "reuters", ok=True)
                log.info("keepalive: re-login automático OK (sin humano)")
            else:
                raise LiveAdapterError("re-login automático no completó (¿slider DataDome?)")
        except Exception as exc:  # noqa: BLE001
            log.error("keepalive Reuters falló: %s", exc)
            alerts.record_run(settings, "reuters", ok=False, error=f"keepalive: {exc}")
        finally:
            try:
                adapter.close()
            except Exception:  # noqa: BLE001
                pass
