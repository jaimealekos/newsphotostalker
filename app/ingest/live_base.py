"""Shared Playwright machinery for browser-driven live adapters.

Only Reuters Connect needs this: it sits behind a DataDome bot-wall that
blocks headless automation and plain HTTP clients, so it requires a real
(headed) browser with a persisted, logged-in profile. AP and Getty are served
over plain HTTP and do NOT use this base.

Key facts verified against the live service (2026-07):
  * DataDome triggers on headless Chromium. Running headed under a virtual
    display (Xvfb) with a normal fingerprint passes. On a server, launch the
    app under ``xvfb-run`` (see README) or set ``playwright.headless: false``
    with a real/virtual display.
  * The login is two-step: type email -> Continue -> type password -> Sign in
    (the password step is served by auth.thomsonreuters.com).
  * A persistent user-data-dir keeps the session, so login happens rarely.
"""

from __future__ import annotations

import queue
import re
import sys
import threading
import time
from pathlib import Path

from .base import BaseAdapter, DownloadedFiles, RawAsset


def en_hilo_sin_bucle(funcion, *args, **kwargs):
    """Ejecuta algo en un hilo recién creado y devuelve su resultado.

    Playwright tiene dos API, una de bloqueo y otra asíncrona, y la de bloqueo
    se niega a funcionar —«Sync API inside the asyncio loop»— si en el hilo
    actual hay un bucle de asyncio corriendo. Quién llama y desde dónde es fácil
    de cambiar sin darse cuenta (una ruta que pasa a ``async``, un ejecutor
    distinto en el planificador), y el fallo solo aparece en tiempo de ejecución.

    Un hilo nuevo nunca tiene bucle, así que envolviendo aquí el trabajo la
    situación deja de poder darse, venga la llamada de donde venga. Las
    excepciones se reenvían al hilo que llamó, para no tragarse ningún error.
    """
    buzon: queue.Queue = queue.Queue(maxsize=1)

    def _corre():
        try:
            buzon.put(("ok", funcion(*args, **kwargs)))
        except BaseException as exc:  # noqa: BLE001 - se relanza tal cual abajo
            buzon.put(("error", exc))

    hilo = threading.Thread(target=_corre, name="playwright", daemon=True)
    hilo.start()
    hilo.join()
    estado, valor = buzon.get()
    if estado == "error":
        raise valor
    return valor

_BG_URL_RE = re.compile(r'url\(["\']?([^"\')]+)')

# Navegadores del sistema que sirven, por orden de preferencia.
#
# Por qué no se usa el Chromium que empaqueta Playwright: en bastantes máquinas
# Windows no arranca en modo headed —falla por SxS ("la configuración en paralelo
# no es correcta") y Playwright lo enmascara como un escueto ``spawn UNKNOWN``—
# y el login de Reuters necesita ventana. Además, un navegador instalado de
# verdad da una huella más creíble para DataDome.
#
# Chrome primero por esa huella; Edge después porque VIENE DE SERIE en todo
# Windows 10 y 11, así que el programa funciona sin instalar nada. Verificado en
# agosto de 2026: Edge arranca con ventana y sin ella, y llega al formulario de
# login de Reuters.
_BROWSERS: dict[str, tuple[tuple[str, str], ...]] = {
    "win32": (
        ("Google Chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        ("Google Chrome", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        ("Google Chrome", r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
        ("Microsoft Edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        ("Microsoft Edge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        ("Microsoft Edge", r"~\AppData\Local\Microsoft\Edge\Application\msedge.exe"),
    ),
    # macOS: Safari no vale (no es Chromium). Se buscan los habituales, también
    # en ~/Applications, donde acaban los que instala el usuario sin permisos.
    "darwin": (
        ("Google Chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ("Google Chrome", "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ("Microsoft Edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ("Brave", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
        ("Chromium", "/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ),
    "linux": (
        ("Google Chrome", "/usr/bin/google-chrome"),
        ("Google Chrome", "/usr/bin/google-chrome-stable"),
        ("Google Chrome", "/opt/google/chrome/chrome"),
        ("Chromium", "/usr/bin/chromium"),
        ("Chromium", "/usr/bin/chromium-browser"),
        ("Chromium", "/snap/bin/chromium"),
        ("Microsoft Edge", "/usr/bin/microsoft-edge"),
    ),
}


def system_browser() -> tuple[str, str] | None:
    """(nombre, ruta) del navegador instalado que se usará, o None si no hay.

    Devolver None NO es fatal fuera de Windows: Playwright tira entonces de su
    propio Chromium, que en macOS y Linux sí arranca con ventana (el fallo SxS
    es exclusivo de Windows). Por eso en Windows se busca con más empeño: allí
    tiene que haber un navegador de verdad, y siempre lo hay porque Edge viene
    de serie.
    """
    for nombre, raw in _BROWSERS.get(_familia(), ()):
        path = Path(raw).expanduser()
        if path.exists():
            return nombre, str(path)
    return None


def _familia() -> str:
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def browser_summary() -> str:
    """Qué navegador se usará, en una línea, para contarlo en el panel."""
    encontrado = system_browser()
    if encontrado:
        return encontrado[0]
    if _familia() == "win32":
        return ""  # en Windows, sin navegador no hay nada que hacer
    return "Chromium (incluido)"


def _launch_hint(exc: Exception, executable: str | None) -> str:
    """Traduce el fallo de arranque a algo accionable en el panel."""
    detail = str(exc).splitlines()[0]
    if not executable and _familia() == "win32":
        return (
            f"{detail} — no se ha encontrado ningún navegador utilizable. Instala "
            "Google Chrome (o Microsoft Edge) y vuelve a intentarlo."
        )
    if not executable:
        return (
            f"{detail} — no hay navegador del sistema y el Chromium incluido no ha "
            "arrancado. En un servidor sin pantalla, arráncalo bajo xvfb-run, o "
            "trae la sesión ya iniciada desde otro equipo (ajustes → importar sesión)."
        )
    return detail


class LiveAdapterError(RuntimeError):
    pass


class LiveAdapter(BaseAdapter):
    """Playwright-backed base (perfil persistente, sin ventanas por defecto)."""

    #: Ventana visible. Solo la pide el login manual (scripts/login_reuters.py),
    #: donde el humano tiene que ver el formulario y el CAPTCHA. Con False manda
    #: ``playwright.headless`` de la configuración, y si aun así toca correr
    #: headed (Linux bajo Xvfb), la ventana se abre fuera de la pantalla para no
    #: aparecer encima de lo que estés haciendo.
    show_window = False

    def __init__(self, settings, credentials):
        super().__init__(settings, credentials)
        self._pw = None
        self._context = None
        self._page = None
        self._logged_in = False

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> None:
        from playwright.sync_api import sync_playwright

        pw_conf = self.settings.playwright
        user_data = Path(pw_conf.user_data_dir)
        if not user_data.is_absolute():
            user_data = (self.settings.data_dir / "browser").resolve()
        profile_dir = user_data / self.agency
        profile_dir.mkdir(parents=True, exist_ok=True)
        # Un crash o reinicio del contenedor deja el lock de instancia única de
        # Chromium en el perfil y todos los arranques posteriores abortan
        # ("Failed to create a ProcessSingleton"). Los runs están serializados
        # (runner._RUN_LOCK), así que aquí nunca hay otro Chromium legítimo
        # sobre este perfil y el lock solo puede ser huérfano.
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            try:
                (profile_dir / name).unlink()
            except OSError:
                pass

        # Huella: NADA de user-agent falso ni parches JS "stealth". DataDome
        # coteja UA vs navigator.platform vs client hints: un UA de Windows
        # sobre un Chromium Linux (o plugins inventados) delata más que el
        # navegador real. Lo único que se toca es el flag AutomationControlled
        # (navigator.webdriver=false) y se quita --enable-automation que
        # Playwright añade por defecto (infobar de "controlado por software").
        # Solo en Linux sin GPU (NAS headless bajo Xvfb) forzamos ANGLE/EGL para
        # que el WebGL salga como "Mesa llvmpipe" en vez de "SwiftShader" (señal
        # de bot típica de headless). En Windows/macOS con Chrome real y GPU se
        # deja el backend NATIVO (ANGLE D3D11/Metal): es la huella normal del
        # navegador del usuario, la que mejor pasa DataDome.
        args = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        if sys.platform.startswith("linux"):
            args += ["--ignore-gpu-blocklist", "--use-gl=angle", "--use-angle=gl-egl"]

        # Verificado 08-2026 con Chrome real y la sesión ya guardada en el
        # perfil: DataDome deja pasar el headless nuevo de Chrome igual que el
        # headed (lo que bloqueaba era el headless del Chromium empaquetado).
        # Así que por defecto no se abre ninguna ventana. Si aun así se corre
        # headed y nadie ha pedido ver la ventana, se manda fuera de pantalla.
        headless = pw_conf.headless and not self.show_window
        if not headless and not self.show_window:
            args.append("--window-position=-32000,-32000")

        launch = dict(
            user_data_dir=str(profile_dir),
            headless=headless,
            args=args,
            ignore_default_args=["--enable-automation"],
            locale="es-ES",
            timezone_id="Europe/Madrid",
            viewport={"width": 1440, "height": 900},
        )
        executable = pw_conf.executable_path
        if not executable:
            encontrado = system_browser()
            executable = encontrado[1] if encontrado else None
        if executable:
            launch["executable_path"] = executable

        self._pw = sync_playwright().start()
        try:
            self._context = self._pw.chromium.launch_persistent_context(**launch)
        except Exception as exc:  # noqa: BLE001 - el motivo real merece explicarse
            raise LiveAdapterError(_launch_hint(exc, executable)) from exc
        self._context.set_default_timeout(pw_conf.timeout_ms)
        self._page = self._context.new_page()

        if self.requires_login:
            self._ensure_login()

    def close(self) -> None:
        try:
            if self._context:
                self._context.close()
        finally:
            if self._pw:
                self._pw.stop()
            self._pw = self._context = self._page = None

    # -- helpers -----------------------------------------------------------
    @property
    def page(self):
        if self._page is None:
            raise LiveAdapterError("browser not opened; call open() first")
        return self._page

    def _ensure_login(self) -> None:
        """Deja el adaptador con sesión, o falla explicando qué falta.

        NO se exigen credenciales aquí: lo normal es que la sesión ya viva en el
        perfil persistente porque el usuario entró a mano una vez, y en ese caso
        no hace falta tener el usuario y la contraseña en ningún fichero. Cada
        adaptador decide en su ``login()`` si le hacen falta.
        """
        if self._logged_in:
            return
        self.login()
        self._logged_in = True

    def login(self) -> None:  # pragma: no cover - overridden
        pass

    def bg_image_url(self, selector: str) -> str | None:
        """Read the CSS background-image URL of an element (lazy previews)."""
        try:
            css = self.page.eval_on_selector(selector, "el => getComputedStyle(el).backgroundImage")
        except Exception:
            return None
        m = _BG_URL_RE.search(css or "")
        return m.group(1) if m else None

    def autoscroll(self, rounds: int = 6, pause: float = 1.4) -> None:
        last = 0
        for _ in range(rounds):
            self.page.mouse.wheel(0, 3000)
            time.sleep(pause)
            h = self.page.evaluate("document.body.scrollHeight")
            if h == last:
                break
            last = h

    # -- download ----------------------------------------------------------
    def download(self, asset: RawAsset, dest_dir) -> DownloadedFiles:
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        headers = {
            "Referer": f"https://www.{self.agency}connect.com/",
            "Origin": f"https://www.{self.agency}connect.com",
        }
        preview_path = thumb_path = None
        total = 0
        if asset.preview_url:
            body = self._fetch(asset.preview_url, headers)
            preview_path = dest / "preview.jpg"
            preview_path.write_bytes(body)
            total += len(body)
        if asset.thumbnail_url and asset.thumbnail_url != asset.preview_url:
            tb = self._fetch(asset.thumbnail_url, headers)
            thumb_path = dest / "thumb.jpg"
            thumb_path.write_bytes(tb)
            total += len(tb)
        elif preview_path:
            thumb_path = preview_path

        if total == 0:
            raise LiveAdapterError(f"{self.agency}: no image for {asset.external_id}")
        return DownloadedFiles(
            preview_path=str(preview_path) if preview_path else None,
            thumbnail_path=str(thumb_path) if thumb_path else None,
            file_bytes=total,
        )

    def _fetch(self, url: str, headers: dict) -> bytes:
        resp = self._context.request.get(url, headers=headers)
        if not resp.ok:
            raise LiveAdapterError(f"download {resp.status} for {url}")
        return resp.body()
