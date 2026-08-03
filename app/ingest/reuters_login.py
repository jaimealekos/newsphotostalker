"""Login manual de Reuters con un navegador NORMAL, sin Playwright.

Reuters Connect está tras DataDome, un muro anti-bot que fichajes la huella de un
navegador automatizado (Playwright conduce Chrome por CDP, y eso se nota) y la
reputación de la IP. Cuando desconfía, planta un CAPTCHA que se atasca y acaba
en «El acceso está restringido temporalmente».

La salida, verificada, es no automatizar el login: se abre el **Chrome (o Edge)
del sistema como un navegador cualquiera** —sin CDP, sin banderas de
automatización— apuntando al mismo perfil persistente que usan las búsquedas. Ahí
DataDome ve un humano y deja resolver el CAPTCHA. La persona inicia sesión y
cierra la ventana; la sesión queda en el perfil y las búsquedas (headless) la
reutilizan sin volver a pedir nada.

Esta lógica la usan el botón de *ajustes* y ``scripts.login_reuters``.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import get_settings
from .reuters import ReutersAdapter

log = logging.getLogger("reuters-login")

#: Tiempo máximo que se deja la ventana abierta para entrar a mano.
DEFAULT_WAIT_MINUTES = 15
#: Espera máxima a que termine una búsqueda en curso (comparten perfil).
LOCK_WAIT_SECONDS = 180


@dataclass
class LoginStatus:
    """Estado del último login, para contarlo en la interfaz."""

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


def _profile_dir(settings) -> Path:
    """El perfil de Reuters, el mismo que abre el adaptador para buscar."""
    user_data = Path(settings.playwright.user_data_dir)
    if not user_data.is_absolute():
        user_data = (settings.data_dir / "browser").resolve()
    profile = user_data / "reuters"
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def open_login_window(wait_minutes: int = DEFAULT_WAIT_MINUTES) -> str:
    """Abre un Chrome/Edge normal en el login de Reuters y espera a que se cierre.

    Se serializa con las búsquedas (comparten el perfil, y Chromium no admite dos
    instancias sobre él). Tras cerrar la ventana, comprueba si la sesión quedó.
    """
    from .live_base import _limpia_locks, en_hilo_sin_bucle, system_browser
    from .runner import _RUN_LOCK

    settings = get_settings()
    navegador = system_browser()
    if not navegador:
        return _fail(
            "no se ha encontrado Chrome ni Edge; instala uno para poder entrar en Reuters"
        )
    binario = navegador[1]
    profile = _profile_dir(settings)

    STATUS.set("waiting_lock", "esperando a que termine lo que esté en curso…")
    if not _RUN_LOCK.acquire(timeout=LOCK_WAIT_SECONDS):
        return _fail("hay una búsqueda en marcha y no ha terminado a tiempo; inténtalo en un minuto")
    try:
        _limpia_locks(profile)
        try:
            # Navegador NORMAL: nada de --enable-automation ni CDP. Un perfil
            # aparte (el de la app), así que no molesta a tu Chrome de siempre.
            proceso = subprocess.Popen(
                [
                    binario,
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "https://www.reutersconnect.com/login",
                ]
            )
        except OSError as exc:
            return _fail(f"no se pudo abrir el navegador: {exc}")

        STATUS.set(
            "open",
            "se ha abierto una ventana de tu navegador: entra en Reuters ahí (resuelve el "
            f"CAPTCHA si aparece) y CIÉRRALA al terminar. Tienes {wait_minutes} min.",
        )
        try:
            proceso.wait(timeout=wait_minutes * 60)
        except subprocess.TimeoutExpired:
            proceso.terminate()
            return _fail("se agotó el tiempo; cierra la ventana en cuanto termines de entrar")

        # La ventana se cerró. ¿Quedó la sesión guardada en el perfil?
        if en_hilo_sin_bucle(_sesion_valida, settings):
            return _ok("sesión iniciada y guardada; las búsquedas ya no pedirán login")
        return _fail(
            "no se detectó la sesión. ¿Entraste del todo (hasta ver tu panel de Reuters) "
            "antes de cerrar la ventana? Si DataDome te bloqueó, espera un rato y reinténtalo."
        )
    except Exception as exc:  # noqa: BLE001 - el botón nunca debe tumbar el servidor
        return _fail(f"{type(exc).__name__}: {exc}")
    finally:
        _RUN_LOCK.release()


def _sesion_valida(settings) -> bool:
    """Abre el perfil headless y comprueba si la sesión de Reuters está viva."""
    adapter = ReutersAdapter(settings, settings.credentials_for("reuters"))
    adapter.requires_login = False  # solo comprobar, no volver a entrar
    adapter.open()
    try:
        page = adapter.page
        page.goto("https://www.reutersconnect.com/all", wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        return adapter._looks_logged_in()
    finally:
        try:
            adapter.close()
        except Exception:  # noqa: BLE001
            pass


def _ok(message: str) -> str:
    STATUS.set("ok", message)
    return message


def _fail(message: str) -> str:
    STATUS.set("error", message)
    return message
