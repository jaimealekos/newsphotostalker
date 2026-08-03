"""Login manual de Reuters con un navegador NORMAL, sin Playwright.

Reuters Connect está tras DataDome, un muro anti-bot que ficha la huella de un
navegador automatizado (Playwright conduce Chrome por CDP) y la reputación de la
IP, y planta un CAPTCHA que se atasca: «El acceso está restringido temporalmente».
La salida es no automatizar el login: se abre el **Chrome/Edge del sistema como un
navegador cualquiera** —sin CDP, sin banderas de automatización— sobre el mismo
perfil que usan las búsquedas. DataDome ve un humano y deja resolver el CAPTCHA.

En DOS pasos, y a propósito. No se puede saber a ciencia cierta cuándo la persona
ha terminado: cerrar la ventana no cierra Chrome si sigue en segundo plano (bandeja
del sistema), así que esperar a que el proceso muera se cuelga. Por eso:

  1. «iniciar sesión»  → abre el navegador y devuelve el control enseguida.
  2. «comprobar»       → cierra ese navegador, mira si la sesión quedó y lo dice.

Multiplataforma: usa el navegador del sistema, así que vale igual en Windows,
macOS y Linux (con pantalla). Sin pantalla, se trae la sesión con exportar/importar.
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

#: Espera máxima a que se libere el perfil de una búsqueda en curso.
LOCK_WAIT_SECONDS = 120

# El navegador que se abrió para el login, para poder cerrarlo al comprobar.
_PROC: subprocess.Popen | None = None
_PROC_LOCK = threading.Lock()


@dataclass
class LoginStatus:
    """Estado del login, para pintarlo en la interfaz.

    state:
      idle          — nada en marcha (o ya terminado; ver message)
      waiting_lock  — esperando a que una búsqueda suelte el perfil
      open          — navegador abierto; el usuario entra y luego pulsa «comprobar»
      checking      — comprobando si la sesión quedó
      ok / error    — resultado del último intento
    """

    state: str = "idle"
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
    def awaiting(self) -> bool:
        """El navegador está abierto y toca que el usuario pulse «comprobar»."""
        return self.state == "open"

    @property
    def busy(self) -> bool:
        """Hay algo en curso; el botón se desactiva."""
        return self.state in ("waiting_lock", "checking")


STATUS = LoginStatus()


def _profile_dir(settings) -> Path:
    """El perfil de Reuters, el mismo que abre el adaptador para buscar."""
    user_data = Path(settings.playwright.user_data_dir)
    if not user_data.is_absolute():
        user_data = (settings.data_dir / "browser").resolve()
    profile = user_data / "reuters"
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def start_login() -> str:
    """Paso 1: abre el navegador normal en el login de Reuters y vuelve."""
    from .live_base import _limpia_locks, system_browser
    from .runner import _RUN_LOCK

    settings = get_settings()
    navegador = system_browser()
    if not navegador:
        return _fail("no se ha encontrado Chrome ni Edge; instala uno para entrar en Reuters")
    binario = navegador[1]
    profile = _profile_dir(settings)

    # Solo se retiene el perfil el instante de arrancar el navegador, no durante
    # el login (que puede tardar). Si hay una búsqueda usándolo, se espera.
    STATUS.set("waiting_lock", "esperando a que termine lo que esté en curso…")
    if not _RUN_LOCK.acquire(timeout=LOCK_WAIT_SECONDS):
        return _fail("hay una búsqueda en marcha; espera a que acabe e inténtalo otra vez")
    try:
        _cierra_navegador()  # por si quedó uno de un intento anterior
        _limpia_locks(profile)
        try:
            proc = subprocess.Popen(
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
        with _PROC_LOCK:
            global _PROC
            _PROC = proc
    finally:
        _RUN_LOCK.release()

    STATUS.set(
        "open",
        "entra en Reuters en la ventana que se ha abierto (resuelve el CAPTCHA si aparece) "
        "hasta ver tu panel, y luego pulsa «he entrado, comprobar».",
    )
    return STATUS.message


def finish_login() -> str:
    """Paso 2: cierra el navegador del login y comprueba si la sesión quedó."""
    from .live_base import _limpia_locks, en_hilo_sin_bucle, matar_navegadores_del_perfil
    from .runner import _RUN_LOCK

    settings = get_settings()
    profile = _profile_dir(settings)

    STATUS.set("checking", "comprobando la sesión…")
    # Primero el lock (así ninguna búsqueda usa el perfil), y SOLO entonces se
    # cierra el navegador del login: se hace bajo el lock para no matar por error
    # el navegador de una búsqueda legítima.
    if not _RUN_LOCK.acquire(timeout=LOCK_WAIT_SECONDS):
        return _fail("hay una búsqueda en marcha; inténtalo en un minuto")
    try:
        _cierra_navegador()  # el que lanzamos, por su identificador
        matar_navegadores_del_perfil(profile)  # y cualquiera que aún tenga el perfil
        _limpia_locks(profile)
        # timeout como red de seguridad: la comprobación no puede colgar el lock.
        ok = en_hilo_sin_bucle(_sesion_valida, settings, timeout_s=90)
    except Exception as exc:  # noqa: BLE001 - el botón nunca debe tumbar el servidor
        return _fail(f"{type(exc).__name__}: {exc}")
    finally:
        _RUN_LOCK.release()

    if ok:
        return _ok("sesión iniciada y guardada; las búsquedas ya no pedirán login")
    return _fail(
        "no se detectó la sesión. Asegúrate de haber entrado del todo (hasta ver tu panel de "
        "Reuters) y vuelve a intentarlo. Si DataDome te bloqueó, espera un rato."
    )


def _cierra_navegador() -> None:
    """Cierra el navegador que se abrió para el login, si sigue vivo."""
    global _PROC
    with _PROC_LOCK:
        proc = _PROC
        _PROC = None
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()


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
