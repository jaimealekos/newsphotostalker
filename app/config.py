"""Application configuration and credential loading.

Config is read from a YAML file (default ``config.local.yaml``, falling back to
``config.example.yaml``). The path can be overridden with the ``APP_CONFIG``
environment variable. Individual values can also be overridden by environment
variables (useful for deployment / secrets managers), e.g. ``REUTERS_PASSWORD``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

# Agencies whose ingestion is routed through the Getty distribution adapter.
GETTY_ROUTED = {"getty", "afp"}

# Canonical list of agencies the system understands.
AGENCIES = ["ap", "reuters", "afp", "getty"]

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- dos raíces distintas, y la diferencia importa al empaquetar -----------
# BUNDLE_DIR  lo que viaja DENTRO del programa y es de solo lectura: plantillas,
#             estáticos, config.example.yaml. Empaquetado vive en _internal/.
# BASE_DIR    lo que es del usuario y hay que poder escribir: config.local.yaml,
#             data/ (base de datos, fotos, perfil del navegador). Empaquetado,
#             la carpeta donde está el .exe; en desarrollo, la del repositorio.
#
# Mezclarlas fue el primer fallo previsto del empaquetado: con PyInstaller,
# __file__ apunta al directorio del bundle, así que los datos se habrían escrito
# ahí en vez de junto al ejecutable.
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", REPO_ROOT))
BASE_DIR = Path(sys.executable).resolve().parent if FROZEN else REPO_ROOT

# La versión "portable" de Windows no es un .exe empaquetado sino un Python
# embebido que un .bat arranca sobre el código. No está FROZEN, pero al usuario
# hay que tratarla igual: escribirle la configuración en el primer arranque en
# vez de caer al ejemplo, que va en modo mock. El .bat lo señala con
# NPS_PORTABLE=1; un desarrollador ejecutando run.py a mano no lleva esa marca y
# no se le toca nada.
# BASE_DIR ya apunta bien (REPO_ROOT = la carpeta del .bat), así que data/ y la
# config caen junto al lanzador sin más cambios.
PORTABLE = FROZEN or bool(os.environ.get("NPS_PORTABLE"))

def _prepara_chromium_empaquetado() -> None:
    """Deja listo el Chromium que viaja dentro (macOS y Linux).

    Llega como ``chromium.tar.gz`` —así el tar conserva el bit de ejecución, que
    PyInstaller pierde al copiar datos— y se desempaqueta UNA vez en
    ``data/browsers``, junto al ejecutable, que es donde sí se puede escribir.

    Hay que apuntar ``PLAYWRIGHT_BROWSERS_PATH`` antes de que nadie importe
    Playwright, y por eso esto vive en el módulo que se carga primero.
    """
    if not FROZEN:
        return
    destino = BASE_DIR / "data" / "browsers"
    tarball = BUNDLE_DIR / "chromium.tar.gz"
    if destino.is_dir() and any(destino.glob("chromium*")):
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(destino))
        return
    if not tarball.is_file():
        return  # paquete sin navegador dentro (Windows): se usa el del sistema
    try:
        import tarfile

        destino.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tarball, "r:gz") as tar:
            tar.extractall(destino)  # noqa: S202 - tarball propio, generado al compilar
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(destino))
    except (OSError, tarfile.TarError):
        pass  # sin navegador incluido; se intentará con el del sistema


_prepara_chromium_empaquetado()


@dataclass
class AgencyCredentials:
    enabled: bool = True
    username: str | None = None
    password: str | None = None

    @property
    def has_login(self) -> bool:
        return bool(self.username and self.password)


@dataclass
class PlaywrightConfig:
    headless: bool = True
    timeout_ms: int = 45000
    user_data_dir: str = "./data/browser"
    # Optional: path to a Chromium/Chrome executable. Leave null to use the
    # browser bundled with Playwright (installed via `playwright install`).
    executable_path: str | None = None


@dataclass
class AlertsConfig:
    """Aviso por flanco cuando una agencia deja de funcionar.

    El aviso se envía en el PRIMER run fallido tras uno bueno (o tras el
    arranque) y no se repite mientras siga fallando; al volver a funcionar el
    disparador se rearma. La entrega es un POST JSON ``{subject, message}`` a
    ``webhook_url`` (apúntalo a cualquier flujo que reenvíe por email).
    """

    enabled: bool = False
    webhook_url: str | None = None
    # Agencias vigiladas. Por defecto solo Reuters: es la única con sesión de
    # navegador que caduca; AP/Getty fallan solo por cortes transitorios.
    agencies: list[str] = field(default_factory=lambda: ["reuters"])
    timeout_s: int = 15
    # Coletilla que se añade al final de TODOS los avisos: instrucciones para
    # quien lo reciba («abre tal cosa», «avisa a tal persona»). Opcional.
    postdata: str | None = None


@dataclass
class Settings:
    mode: str = "mock"  # "mock" | "live"
    data_dir: Path = field(default_factory=lambda: BASE_DIR / "data")
    db_url: str = "sqlite:///./data/app.db"
    default_cadence_minutes: int = 360
    # Refresco periódico de la sesión de Reuters para que no caduque. Cada ciclo
    # hace una búsqueda real, que es lo que renueva el token de auth0 (no solo la
    # cookie de datadome). Debe ir por DEBAJO de la vida del token (~24 h), así
    # que 60 min sobra; 0 = desactivado. Ver app/ingest/keepalive.py.
    reuters_keepalive_minutes: int = 60
    agencies: dict[str, AgencyCredentials] = field(default_factory=dict)
    playwright: PlaywrightConfig = field(default_factory=PlaywrightConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)

    # --- convenience -------------------------------------------------------
    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    def credentials_for(self, agency: str) -> AgencyCredentials:
        """Return credentials for an agency, routing AFP -> getty config."""
        key = "getty" if agency in GETTY_ROUTED else agency
        return self.agencies.get(key, AgencyCredentials())

    @property
    def is_live(self) -> bool:
        return self.mode == "live"


def _config_path() -> Path:
    """La configuración que manda: la del usuario, junto al ejecutable.

    Empaquetado, si no existe se crea en el primer arranque (ver
    :func:`_write_first_run_config`), porque el ejemplo que viaja dentro arranca
    en modo ``mock`` y un recién llegado se encontraría con fotos inventadas.
    """
    override = os.environ.get("APP_CONFIG")
    if override:
        return Path(override)
    local = BASE_DIR / "config.local.yaml"
    if not local.exists() and PORTABLE:
        _write_first_run_config(local)
    if local.exists():
        return local
    return BUNDLE_DIR / "config.example.yaml"


FIRST_RUN_CONFIG = """\
# newsphotostalker — configuración local.
# Se creó sola en el primer arranque; edítala y reinicia el programa.
# Tus fotos y tu base de datos están en la carpeta data/, aquí al lado.

# live = fotos reales de las agencias. mock = fotos sintéticas, para probar.
mode: live

data_dir: ./data
db_url: sqlite:///./data/app.db

agencies:
  # AP y Getty (y AFP, que se distribuye por Getty) no piden credenciales.
  ap:      {enabled: true}
  getty:   {enabled: true}
  # Reuters SÍ necesita tu cuenta, pero NO se escribe aquí: entra desde
  # «ajustes → iniciar sesión en Reuters», que abre una ventana del navegador y
  # deja la sesión guardada. El programa nunca teclea tu contraseña.
  reuters: {enabled: true, username: null, password: null}

playwright:
  # true = las búsquedas corren SIN abrir ninguna ventana. La única ventana que
  # verás es la del login manual de Reuters, que la abre igualmente.
  headless: true
  timeout_ms: 45000
  user_data_dir: ./data/browser
  # null = usa el Google Chrome instalado (necesario para el login de Reuters).
  executable_path: null

# Aviso opcional por webhook cuando una agencia deja de funcionar.
alerts:
  enabled: false
  webhook_url: null
"""


def _read_config(path: Path) -> str:
    """Lee la configuración sin depender de la codificación regional.

    ``read_text()`` a secas usa la del sistema, que en un Windows español es
    cp1252, y la config que escribe el programa va en UTF-8: sin esto, el .exe
    moría en el primer arranque leyendo su propio fichero recién creado. Se
    prueba también cp1252 por si alguien la ha reescrito con el Bloc de notas.
    """
    for encoding in ("utf-8", "cp1252"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _write_first_run_config(path: Path) -> None:
    """Deja una config editable junto al ejecutable la primera vez."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(FIRST_RUN_CONFIG, encoding="utf-8")
    except OSError:
        # Carpeta de solo lectura (p. ej. Archivos de programa): se sigue con el
        # ejemplo empaquetado en vez de reventar el arranque.
        pass


def _env_override(agency: str, cred: AgencyCredentials) -> AgencyCredentials:
    """Allow REUTERS_USERNAME / REUTERS_PASSWORD style env overrides."""
    prefix = agency.upper()
    user = os.environ.get(f"{prefix}_USERNAME")
    pw = os.environ.get(f"{prefix}_PASSWORD")
    if user:
        cred.username = user
    if pw:
        cred.password = pw
    return cred


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    path = _config_path()
    raw: dict = {}
    if path.exists():
        raw = yaml.safe_load(_read_config(path)) or {}

    data_dir = Path(raw.get("data_dir", "./data"))
    if not data_dir.is_absolute():
        data_dir = (BASE_DIR / data_dir).resolve()

    agencies: dict[str, AgencyCredentials] = {}
    for name, conf in (raw.get("agencies") or {}).items():
        conf = conf or {}
        cred = AgencyCredentials(
            enabled=bool(conf.get("enabled", True)),
            username=conf.get("username"),
            password=conf.get("password"),
        )
        agencies[name] = _env_override(name, cred)

    pw_conf = raw.get("playwright") or {}
    playwright = PlaywrightConfig(
        headless=bool(pw_conf.get("headless", True)),
        timeout_ms=int(pw_conf.get("timeout_ms", 45000)),
        user_data_dir=pw_conf.get("user_data_dir", "./data/browser"),
        executable_path=os.environ.get("PW_EXECUTABLE_PATH", pw_conf.get("executable_path")),
    )

    al_conf = raw.get("alerts") or {}
    alerts = AlertsConfig(
        enabled=bool(al_conf.get("enabled", False)),
        webhook_url=al_conf.get("webhook_url"),
        agencies=list(al_conf.get("agencies") or ["reuters"]),
        timeout_s=int(al_conf.get("timeout_s", 15)),
        postdata=al_conf.get("postdata"),
    )

    settings = Settings(
        mode=os.environ.get("INGEST_MODE", raw.get("mode", "mock")),
        data_dir=data_dir,
        db_url=raw.get("db_url", "sqlite:///./data/app.db"),
        default_cadence_minutes=int(raw.get("default_cadence_minutes", 360)),
        reuters_keepalive_minutes=int(raw.get("reuters_keepalive_minutes", 60)),
        agencies=agencies,
        playwright=playwright,
        alerts=alerts,
    )
    # Make sure the media directory exists early.
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    return settings


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
