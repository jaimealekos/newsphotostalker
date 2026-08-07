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

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import alerts
from ..config import get_settings
from .runner import _RUN_LOCK

log = logging.getLogger("keepalive")

WARM_URL = "https://www.reutersconnect.com/all"

#: Clave del vigilante en el fichero de avisos.
CLAVE_VIGILANCIA = "keepalive"

#: Cuántos intervalos puede pasar sin dar señal antes de dar la voz de alarma.
#: Dos, para que un retraso puntual o un reinicio no disparen nada.
INTERVALOS_DE_GRACIA = 2


# --- señal de vida, y quién la vigila ---------------------------------------
#
# El keep-alive era la única pieza que no dejaba rastro en ninguna parte: las
# búsquedas escriben su fila en `run_logs`, él solo escribía una línea de
# registro. Sus FALLOS se ven (van a error, y disparan el aviso), pero su
# AUSENCIA no: si el trabajo dejara de programarse, el silencio sería idéntico
# al de un keep-alive impecable, y el primer síntoma llegaría días después, con
# la sesión de Reuters caducada. Que es exactamente la avería que ya pasó.
#
# Así que deja señal fechada al funcionar, y alguien la mira.


def _ruta_senal(settings) -> Path:
    return Path(settings.data_dir) / "keepalive_state.json"


def marca_senal(settings, motivo: str = "ok") -> None:
    """Anota que el keep-alive ha dado señal de vida ahora mismo."""
    ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        ruta = _ruta_senal(settings)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps({"ultima_senal": ahora, "motivo": motivo}, indent=2))
    except OSError as exc:  # noqa: BLE001 - la señal es un extra, nunca tumba el run
        log.warning("no se pudo anotar la señal del keep-alive: %s", exc)


def ultima_senal(settings) -> datetime | None:
    """Cuándo dio señal por última vez, o None si nunca se ha anotado."""
    try:
        dato = json.loads(_ruta_senal(settings).read_text()).get("ultima_senal")
        return datetime.fromisoformat(dato) if dato else None
    except (OSError, ValueError, AttributeError):
        return None


def revisa_atraso(settings=None) -> bool:
    """Vigila al vigilante. Devuelve True si el keep-alive lleva demasiado callado.

    Se llama desde el refresco global —otro trabajo distinto del planificador—,
    así que detecta el caso realista: que el keep-alive haya dejado de
    programarse mientras el resto sigue funcionando. Si muriera el planificador
    ENTERO no saltaría, pero entonces tampoco correrían las búsquedas y eso sí
    se ve en el panel a simple vista.
    """
    settings = settings or get_settings()
    minutos = settings.reuters_keepalive_minutes
    if not minutos:
        return False  # desactivado a propósito: no hay nada que vigilar

    ultima = ultima_senal(settings)
    if ultima is None:
        # Sin referencia todavía (instalación nueva, o el fichero se borró): se
        # toma este instante como punto de partida en vez de avisar a ciegas.
        marca_senal(settings, motivo="referencia inicial")
        return False

    limite = timedelta(minutes=minutos * INTERVALOS_DE_GRACIA)
    atraso = datetime.now(timezone.utc) - ultima
    if atraso <= limite:
        alerts.vigila(settings, CLAVE_VIGILANCIA, True, "", "")
        return False

    horas = atraso.total_seconds() / 3600
    log.error(
        "el keep-alive de Reuters no da señal desde %s (%.1f h, el límite son %s min)",
        ultima.isoformat(timespec="minutes"), horas, int(limite.total_seconds() // 60),
    )
    alerts.vigila(
        settings,
        CLAVE_VIGILANCIA,
        False,
        "[newsphotostalker] el keep-alive de Reuters no se está ejecutando",
        (
            f"El keep-alive lleva sin dar señal desde {ultima.isoformat(timespec='minutes')} "
            f"({horas:.1f} h), y debería hacerlo cada {minutos} min.\n\n"
            "No es que Reuters falle: es que lo que EVITA que falle no se está "
            "ejecutando. Si no se corrige, la sesión caducará por su cuenta y "
            "habrá que rehacer el login a mano.\n\n"
            "Mira el registro del programa por si el planificador no arrancó, y "
            "que reuters_keepalive_minutes siga puesto en la configuración."
        ),
    )
    return True


def keepalive_reuters() -> None:
    settings = get_settings()
    cred = settings.credentials_for("reuters")
    if not cred.enabled or not cred.has_login:
        return

    from .live_base import en_hilo_sin_bucle

    with _RUN_LOCK:
        # En un hilo recién creado, por lo mismo que el login y las búsquedas: la
        # API de bloqueo de Playwright no arranca si en el hilo actual hay un
        # bucle de asyncio, y este trabajo lo dispara el planificador, que
        # reutiliza sus hilos. Sin esto el keep-alive muere con «Sync API inside
        # the asyncio loop» — y muere callado, que es lo peor que puede pasarle:
        # es justo lo que evita que caduque la sesión de Reuters.
        en_hilo_sin_bucle(_keepalive_locked, settings, cred)


def _keepalive_locked(settings, cred) -> None:
    """El trabajo del keep-alive. Se llama con el lock tomado y en hilo limpio."""
    from .live_base import LiveAdapterError
    from .reuters import ReutersAdapter

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
            marca_senal(settings)
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
            marca_senal(settings, motivo="re-login automático")
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
