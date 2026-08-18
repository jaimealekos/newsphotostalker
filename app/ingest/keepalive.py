"""Mantiene viva la sesión de Reuters para no repetir el login humano.

Reuters Connect exige login humano UNA vez (el slider de DataDome no se
automatiza). Para que esa sesión no caduque, este keep-alive carga
periódicamente la home logueada: eso renueva la cookie ``datadome`` y dispara
la renovación silenciosa del token auth0. Mientras la sesión siga viva, las
ejecuciones normales pasan el bot-wall sin re-login. Cargar la página NO es un
inicio de sesión: no teclea ninguna contraseña, solo navega.

Si la sesión ha caído, el keep-alive NO la rehace: se limita a avisar. Antes
intentaba un re-login automático (email+password), y resultó ser veneno —cada
intento fallido contra el muro DataDome cuenta como «unsuccessful sign-in
attempt» y Reuters acabó bloqueando la cuenta por IP (08-2026)—. Rehacer la
sesión es cosa de un humano, desde «ajustes → iniciar sesión en Reuters».

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

#: Término neutro para la búsqueda de calentamiento. No importa qué devuelva: lo
#: que cuenta es que sea una petición AUTENTICADA de verdad, que es lo que obliga
#: a renovar el token (ver _keepalive_locked).
WARM_QUERY = "news"

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
    if not hay_sesion_guardada(settings):
        # Sin sesión que mantener, el keep-alive no corre A PROPÓSITO: avisar de
        # su silencio sería mandar a un recién instalado —que quizá ni usa
        # Reuters— a depurar un planificador que está perfectamente.
        return False

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


def hay_sesion_guardada(settings) -> bool:
    """¿Alguien ha hecho login aquí alguna vez? (= hay perfil de navegador).

    Es la condición para que el keep-alive trabaje —y para que su vigilante
    vigile—. Antes se exigía tener usuario y contraseña en la configuración,
    pero eso ya no tiene sentido: el programa no los usa para nada (el login es
    siempre manual), y obligaba a guardar la contraseña en un fichero solo para
    encender el keep-alive.
    """
    from .live_base import perfil_del_navegador

    perfil = perfil_del_navegador(settings)
    try:
        # No basta con que exista: el botón de login crea el directorio ANTES
        # de abrir el navegador, así que uno vacío solo dice que alguien pulsó.
        return perfil.is_dir() and any(perfil.iterdir())
    except OSError:
        return False


# --- cuánto vive una sesión: la medida que faltaba --------------------------
#
# Cada login humano deja fecha. Cuando la sesión muere, el aviso dice cuántos
# días aguantó: con dos o tres ciclos reales sabremos la vida REAL de la sesión
# de Reuters (que no es pública) y podremos avisar ANTES de que caduque en vez
# de después, con datos y no con supuestos.


def _ruta_login_humano(settings) -> Path:
    return Path(settings.data_dir) / "reuters_sesion.json"


def marca_login_humano(settings, via: str = "login manual") -> None:
    """Anota que un humano acaba de dejar una sesión de Reuters viva."""
    ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        ruta = _ruta_login_humano(settings)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps({"ultimo_login_humano": ahora, "via": via}, indent=2))
    except OSError as exc:  # noqa: BLE001 - la medida es un extra, nunca rompe el login
        log.warning("no se pudo anotar el login humano: %s", exc)


def dias_desde_login_humano(settings) -> float | None:
    """Días desde el último login humano, o None si nunca se anotó."""
    try:
        dato = json.loads(_ruta_login_humano(settings).read_text()).get("ultimo_login_humano")
        if not dato:
            return None
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(dato)
        return delta.total_seconds() / 86400
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def sesion_ejercitada(settings, agency: str, ok: bool) -> None:
    """La llama el runner tras cada ejecución: una búsqueda REAL de Reuters que
    termina bien es exactamente el trabajo del keep-alive (misma sesión, mismo
    navegador, petición autenticada), así que cuenta como su señal. El tick
    siguiente se la encuentra fresca y se ahorra lanzar un Chromium para nada.
    """
    if agency == "reuters" and ok:
        marca_senal(settings, motivo="búsqueda real")


def keepalive_reuters() -> None:
    settings = get_settings()
    cred = settings.credentials_for("reuters")
    if not cred.enabled or not hay_sesion_guardada(settings):
        return

    # Si la sesión acaba de ejercitarse (este mismo keep-alive, o una búsqueda
    # real vía sesion_ejercitada), lanzar otro navegador no aporta nada.
    minutos = settings.reuters_keepalive_minutes or 60
    ultima = ultima_senal(settings)
    if ultima is not None:
        frescura = (datetime.now(timezone.utc) - ultima).total_seconds() / 60
        if frescura < minutos / 2:
            log.info("keepalive: señal fresca (%.0f min); sin trabajo que hacer", frescura)
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
    from .reuters import ReutersAdapter

    adapter = ReutersAdapter(settings, cred)
    adapter.requires_login = False  # open() no debe forzar el login
    try:
        adapter.open()
        page = adapter.page
        page.goto(WARM_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # Quién decide es el CLASIFICADOR del adaptador, no la URL a secas:
        # DataDome sirve su challenge en la misma URL, así que mirar solo la URL
        # tomaba un muro transitorio por una sesión sana y anotaba éxito falso.
        estado = adapter.estado_sesion()

        if estado == "viva":
            # Una BÚSQUEDA real (no un simple scroll) es lo que de verdad alarga
            # la sesión: obliga a la SPA a ejercitar la renovación silenciosa del
            # token de auth0 y a reiniciar la ventana de vida del refresh token.
            # Cargar la home y hacer scroll solo refrescaba la cookie de DataDome,
            # y dejaba morir el token a las pocas horas —de ahí que hubiera que
            # re-loguear tan seguido—.
            try:
                adapter.search(kind="text", query=WARM_QUERY, since=None, limit=1)
            except Exception as exc:  # noqa: BLE001
                # Que la búsqueda tropiece puede ser el propio muro apareciendo a
                # mitad: reclasificar antes de dar la sesión por ejercitada.
                if adapter._datadome_challenge():
                    estado = "challenge"
                else:
                    log.info("keepalive: la búsqueda de calentamiento no rindió (%s)", exc)

        if estado == "viva":
            alerts.record_run(settings, "reuters", ok=True)
            marca_senal(settings)
            log.info("keepalive: sesión Reuters viva")
            return

        if estado == "challenge":
            # Muro TRANSITORIO, no una sesión muerta: ni éxito ni aviso de
            # re-login; se reintenta al próximo ciclo. La señal SÍ se marca:
            # significa «el keep-alive corrió», que es lo que vigila el
            # vigilante — sin ella, un muro persistente haría saltar la alarma
            # de «el keep-alive no se está ejecutando», que es mentira.
            marca_senal(settings, motivo="challenge de DataDome")
            log.warning("keepalive: challenge transitorio de DataDome; reintento en el próximo ciclo")
            return

        # Sesión caída de verdad. NO se re-loguea: automatizar el login teclea la
        # contraseña contra el muro DataDome, y cada intento fallido bloquea la
        # cuenta por IP (pasó en 08-2026). El keep-alive solo MANTIENE viva una
        # sesión ya abierta; rehacerla es cosa de un humano, desde «ajustes →
        # iniciar sesión en Reuters». Aquí solo se avisa — diciendo cuántos días
        # aguantó, que es el dato que calibra cuándo conviene avisar por adelantado.
        marca_senal(settings, motivo="sesión caída")  # el keep-alive corrió; otra cosa es lo que encontró
        edad = dias_desde_login_humano(settings)
        duracion = f"la sesión ha aguantado {edad:.1f} días. " if edad is not None else ""
        log.warning("keepalive: sesión de Reuters caída; hace falta login manual. %s", duracion)
        alerts.record_run(
            settings,
            "reuters",
            ok=False,
            error=(
                f"keepalive: sesión caída; {duracion}"
                "entra a mano desde «ajustes → iniciar sesión en Reuters»"
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log.error("keepalive Reuters falló: %s", exc)
        alerts.record_run(settings, "reuters", ok=False, error=f"keepalive: {exc}")
    finally:
        try:
            adapter.close()
        except Exception:  # noqa: BLE001
            pass
