"""Login manual de Reuters: abre la ventana y espera a que entre una persona.

Es el único momento en que newsphotostalker enseña un navegador. Reuters Connect
exige sesión y está tras DataDome, cuyo desafío no se puede automatizar de forma
fiable, así que el usuario entra a mano UNA vez (email, contraseña y el
deslizador si aparece) y la sesión queda en el perfil persistente. A partir de
ahí las ejecuciones van en headless y **no hacen falta las credenciales en
ningún fichero**.

Aquí vive la lógica; la usan tanto ``scripts.login_reuters`` (línea de órdenes)
como el botón de *ajustes*, para que el programa empaquetado no dependa de un
.bat suelto.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import get_settings
from .reuters import ReutersAdapter

log = logging.getLogger("reuters-login")

#: Espera máxima a que la persona complete el login.
DEFAULT_WAIT_MINUTES = 9
#: Espera máxima a que termine una búsqueda en curso (comparten navegador).
LOCK_WAIT_SECONDS = 180


@dataclass
class LoginStatus:
    """Estado del último login manual, para poder contarlo en la interfaz."""

    state: str = "idle"  # idle|waiting_lock|open|ok|error
    message: str = ""
    updated_at: datetime | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, state: str, message: str) -> None:
        with self._lock:
            self.state = state
            self.message = message
            self.updated_at = datetime.now(timezone.utc)
        log.info("login de Reuters: %s — %s", state, message)

    @property
    def running(self) -> bool:
        return self.state in ("waiting_lock", "open")


STATUS = LoginStatus()


def open_login_window(wait_minutes: int = DEFAULT_WAIT_MINUTES) -> str:
    """Abre Chrome en el login de Reuters y espera a que la sesión aparezca.

    Se serializa con las búsquedas mediante el lock del runner: Chromium no
    admite dos instancias sobre el mismo perfil, así que si hay una ejecución en
    marcha se espera a que acabe en vez de reventar con un error de perfil.
    """
    from .live_base import en_hilo_sin_bucle
    from .runner import _RUN_LOCK

    STATUS.set("waiting_lock", "esperando a que termine lo que esté en curso…")
    if not _RUN_LOCK.acquire(timeout=LOCK_WAIT_SECONDS):
        return _fail("hay una búsqueda en marcha y no ha terminado a tiempo; inténtalo en un minuto")
    try:
        # En hilo propio: Playwright de bloqueo no admite un bucle de asyncio en
        # el hilo actual, y quién nos llama no debería poder romper esto.
        return en_hilo_sin_bucle(_login, wait_minutes)
    except Exception as exc:  # noqa: BLE001 - el botón nunca debe tumbar el servidor
        return _fail(f"{type(exc).__name__}: {exc}")
    finally:
        _RUN_LOCK.release()


def _login(wait_minutes: int) -> str:
    settings = get_settings()
    adapter = ReutersAdapter(settings, settings.credentials_for("reuters"))
    # Que open() no dispare el login automático: aquí manda la persona.
    adapter.requires_login = False
    # Y que la ventana se vea, aunque la configuración diga headless.
    adapter.show_window = True

    adapter.open()
    try:
        page = adapter.page
        page.goto("https://www.reutersconnect.com/all", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        if adapter._looks_logged_in():
            return _ok("ya había sesión guardada: no hacía falta entrar de nuevo")

        page.goto("https://www.reutersconnect.com/login", wait_until="domcontentloaded")
        STATUS.set(
            "open",
            f"ventana abierta: entra en Reuters a mano (tienes {wait_minutes} minutos)",
        )

        deadline = time.time() + wait_minutes * 60
        while time.time() < deadline:
            time.sleep(3)
            urls = _open_urls(adapter)
            if urls is None:
                return _fail("se cerró el navegador antes de detectar la sesión")
            if any(_is_signed_in(u) for u in urls):
                time.sleep(4)  # que la página asiente antes de cerrar el perfil
                return _ok("sesión iniciada y guardada; las búsquedas ya no pedirán login")
        return _fail("se agotó la espera sin detectar la sesión; vuelve a intentarlo")
    finally:
        try:
            adapter.close()
        except Exception:  # noqa: BLE001
            pass


def _open_urls(adapter) -> list[str] | None:
    """URLs de las pestañas abiertas, o None si el navegador ya no está."""
    try:
        pages = adapter._context.pages
    except Exception:  # noqa: BLE001
        return None
    if not pages:
        return None
    urls = []
    for page in pages:
        try:
            urls.append(page.url)
        except Exception:  # noqa: BLE001
            continue
    return urls


def _is_signed_in(url: str) -> bool:
    return (
        "reutersconnect.com" in url
        and "/login" not in url
        and "auth.thomsonreuters.com" not in url
    )


def _ok(message: str) -> str:
    STATUS.set("ok", message)
    return message


def _fail(message: str) -> str:
    STATUS.set("error", message)
    return message
