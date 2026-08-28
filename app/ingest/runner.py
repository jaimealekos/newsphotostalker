"""Ingest runner — orchestrates one run of a search.

Steps per run:
  1. open the agency adapter,
  2. search for assets newer than the search cursor (the "novedades" logic),
  3. skip assets already stored (de-dup by external_id),
  4. download preview + thumbnail and persist the asset + metadata,
  5. advance the cursor, record status,
  6. apply the retention policy (purge oldest / over-limit),
  7. write a RunLog row.

A module-level lock serialises runs so concurrent scheduler jobs don't fight
over the SQLite database.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta
from sqlalchemy import select

from .. import alerts, storage
from ..config import get_settings
from ..database import session_scope
from ..models import RETENTION_TIME, Asset, RunLog, Search, utcnow
from ..retention import purge_search
from .factory import get_adapter

log = logging.getLogger("runner")

_RUN_LOCK = threading.Lock()

#: Intentos por ejecución: el segundo es el reintento ante un fallo pasajero.
#:
#: Por qué: una sola página que tarda de más —Reuters lento, un interstitial
#: momentáneo del muro anti-bot— daba una ejecución fallida y, con los avisos
#: activados, un correo. Visto en producción: 52 ejecuciones de Reuters en 24 h,
#: **una** fallida por un `wait_for_selector` agotado, y la siguiente ronda ya
#: iba bien. El reintento distingue eso de que la agencia esté rota de verdad,
#: y no cede nada a cambio: si el segundo intento también falla, la ejecución se
#: marca como fallida y el aviso sale igual.
INTENTOS = 2

#: Espera entre intentos. No es solo cortesía: si el fallo vino de un muro
#: anti-bot, volver a golpear en el mismo segundo es la peor idea posible.
ESPERA_REINTENTO_S = 15

#: Fallos que NO se reintentan, porque reintentarlos no puede arreglarlos y sí
#: hacer daño: sin credenciales seguirá sin haberlas, y ante un bot-wall que
#: exige un humano, insistir solo empeora la reputación del navegador.
#: Es la red de seguridad por TEXTO; la vía principal es que la propia
#: excepción diga si un reintento sirve (``reintentable``, ver live_base):
#: casar cadenas resultó frágil —cambiar una palabra del mensaje reactivó un
#: reintento dañino sin que ningún test lo viera—.
NO_REINTENTABLES = (
    "no se completó",
    "needs credentials",
    "necesita credenciales",
    "no hay sesión",
)

# Centinela: "usa el cursor de novedades de la búsqueda" (por defecto). El
# backfill pasa un `since` explícito (la fecha límite de retención, o None).
_USE_CURSOR = object()

# Backfill (relleno bajo demanda): tope de páginas por adaptador y de fotos
# totales, para que baje hasta el límite de retención sin desbocarse.
BACKFILL_PAGE_CAP = 60
BACKFILL_MAX = 3000


@dataclass
class RunResult:
    search_id: int
    status: str
    new_assets: int = 0
    purged_assets: int = 0
    found: int = 0
    message: str = ""
    errors: list[str] = field(default_factory=list)


def run_search(
    search_id: int, limit: int = 100, *, since=_USE_CURSOR, page_cap=None, avisa: bool = True
) -> RunResult:
    """Run a single search end-to-end. Thread-safe (serialised).

    Por defecto usa el cursor de novedades (solo trae lo más nuevo). El backfill
    pasa ``since`` explícito (fecha límite de retención o None) y un ``page_cap``
    alto para bajar hacia atrás y rellenar toda la ventana.

    ``avisa=False`` deja el aviso de la agencia para quien llama: lo usa
    :func:`run_all_enabled`, que avisa una vez por lote (ver allí el porqué).

    Se ejecuta en un hilo recién creado (``en_hilo_sin_bucle``) por lo mismo que
    el login: la API de bloqueo de Playwright se niega a arrancar —«Sync API
    inside the asyncio loop»— si en el hilo actual hay un bucle de asyncio. Aquí
    la llamada viene del planificador, cuyos hilos se REUTILIZAN, así que basta
    con que una ejecución anterior dejara un bucle puesto en ese hilo para que la
    siguiente búsqueda de Reuters falle. El síntoma es desconcertante: unas
    búsquedas van y otras no, sin patrón, y cambia de una vez a otra.
    """
    from .live_base import en_hilo_sin_bucle

    with _RUN_LOCK:
        # Sin timeout: un backfill largo puede tardar minutos y no es un cuelgue.
        return en_hilo_sin_bucle(_run_search_locked, search_id, limit, since, page_cap, avisa)


def backfill_search(search_id: int) -> RunResult:
    """Rellena el histórico de una búsqueda hasta su límite de retención.

    Ignora el cursor de novedades y baja hasta la fecha de retención (o, con
    retención por espacio, hasta ``BACKFILL_MAX``); el de-dup por external_id
    hace que solo se descargue lo que aún no está guardado.
    """
    with session_scope() as session:
        search = session.get(Search, search_id)
        floor = _retention_floor(search) if search is not None else None
    return run_search(search_id, limit=BACKFILL_MAX, since=floor, page_cap=BACKFILL_PAGE_CAP)


def _retention_floor(search: Search) -> datetime | None:
    """Fecha más antigua que la retención conservaría (None si es por espacio)."""
    if search.retention_mode == RETENTION_TIME and search.retention_months:
        return datetime.now(timezone.utc) - relativedelta(months=search.retention_months)
    return None


def con_reintento(trabajo, etiqueta: str = ""):
    """Ejecuta ``trabajo()``; ante un fallo pasajero lo intenta una vez más.

    Si el segundo intento también falla, la excepción sube tal cual: el aviso y
    la ejecución fallida salen igual que antes. Lo único que se filtra es el
    ruido de un tropiezo aislado.
    """
    for intento in range(1, INTENTOS + 1):
        try:
            return trabajo()
        except Exception as exc:  # noqa: BLE001 - se relanza abajo si toca
            if intento >= INTENTOS or not _merece_reintento(exc):
                raise
            log.warning(
                "%s falló (%s); reintento en %s s", etiqueta or "la ingesta", exc,
                ESPERA_REINTENTO_S,
            )
            time.sleep(ESPERA_REINTENTO_S)


def _merece_reintento(exc: Exception) -> bool:
    """¿Este fallo puede arreglarse volviendo a intentarlo dentro de un rato?

    Casi todos sí: son tiempos de espera agotados y páginas que no llegaron a
    pintar. Primero se le pregunta a la propia excepción (``reintentable``,
    p. ej. :class:`~.live_base.SinSesionError` dice que no); el cotejo por
    texto de :data:`NO_REINTENTABLES` queda como red para los errores que no
    llevan esa marca.
    """
    if getattr(exc, "reintentable", True) is False:
        return False
    texto = str(exc).lower()
    return not any(marca.lower() in texto for marca in NO_REINTENTABLES)


def _run_search_locked(search_id: int, limit: int, since, page_cap, avisa: bool = True) -> RunResult:
    settings = get_settings()

    with session_scope() as session:
        search = session.get(Search, search_id)
        if search is None:
            return RunResult(search_id, "error", message="search not found")
        # snapshot the fields we need after the session closes
        agency = search.agency
        kind = search.kind
        query = search.query
        cursor = search.cursor
        existing_ids = set(
            session.scalars(select(Asset.external_id).where(Asset.search_id == search_id))
        )
        run = RunLog(search_id=search_id, status="running", started_at=utcnow())
        session.add(run)
        session.flush()
        run_id = run.id

    since_for_search = cursor if since is _USE_CURSOR else since
    result = RunResult(search_id=search_id, status="ok")
    new_cursor = cursor

    def _una_pasada() -> None:
        """Un intento completo: abre el adaptador, busca y guarda lo nuevo."""
        nonlocal new_cursor
        adapter = get_adapter(agency, settings)
        with adapter:
            # Backfill: sube el tope de páginas del adaptador (AP/Getty) para
            # poder bajar hasta el límite de retención en una sola pasada.
            if page_cap is not None:
                try:
                    adapter.MAX_PAGES = page_cap
                except Exception:  # noqa: BLE001
                    pass
            found = adapter.search(kind=kind, query=query, since=since_for_search, limit=limit)
            result.found = len(found)

            for raw in found:
                if raw.external_id in existing_ids:
                    continue
                try:
                    _store_asset(adapter, search_id, agency, raw)
                    existing_ids.add(raw.external_id)
                    result.new_assets += 1
                    cap = _as_aware(raw.captured_at)
                    if cap and (new_cursor is None or cap > _as_aware(new_cursor)):
                        new_cursor = cap
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"{raw.external_id}: {exc}")

    try:
        con_reintento(_una_pasada, f"{agency}/{query}")
    except Exception as exc:  # noqa: BLE001
        result.status = "error"
        result.message = f"{type(exc).__name__}: {exc}"
        result.errors.append(traceback.format_exc(limit=3))

    # Persist run outcome + retention purge.
    with session_scope() as session:
        search = session.get(Search, search_id)
        if search is not None:
            search.last_run_at = utcnow()
            search.last_status = result.status
            search.last_error = result.message or None
            if new_cursor is not None:
                search.cursor = _as_aware(new_cursor)
            try:
                result.purged_assets = purge_search(session, search)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"purge: {exc}")

        run = session.get(RunLog, run_id)
        if run is not None:
            run.finished_at = utcnow()
            run.status = result.status
            run.new_assets = result.new_assets
            run.purged_assets = result.purged_assets
            run.message = _summarise(result)

    # Aviso por flanco (solo agencias vigiladas; ver app/alerts.py). Nunca debe
    # tumbar el run: el aviso es un extra, el run ya está registrado. En un lote
    # ni se toca: el veredicto entero lo manda run_all_enabled (avisa=False).
    if avisa:
        _avisa_de_un_run(settings, agency, result)

    # Una búsqueda real de Reuters que va bien ES el trabajo del keep-alive:
    # cuenta como su señal, y el próximo tick se ahorra lanzar un navegador.
    try:
        from .keepalive import sesion_ejercitada

        sesion_ejercitada(settings, agency, result.status != "error")
    except Exception:  # noqa: BLE001
        pass

    result.message = _summarise(result)
    return result


def _store_asset(adapter, search_id: int, agency: str, raw) -> None:
    directory = storage.asset_dir(agency, search_id, raw.external_id)
    # Reuse the already-open adapter (live adapters keep a single browser open
    # for the whole run) so downloads share the authenticated session.
    files = adapter.download(raw, directory)

    metadata = {
        "external_id": raw.external_id,
        "agency": raw.agency,
        "title": raw.title,
        "caption": raw.caption,
        "photographer": raw.photographer,
        "credit": raw.credit,
        "captured_at": raw.captured_at.isoformat() if raw.captured_at else None,
        "keywords": raw.keywords,
        "detail_url": raw.detail_url,
        "raw_metadata": raw.raw_metadata,
    }
    storage.write_metadata(directory, metadata)

    with session_scope() as session:
        asset = Asset(
            search_id=search_id,
            agency=agency,
            external_id=raw.external_id,
            title=raw.title,
            caption=raw.caption,
            photographer=raw.photographer,
            credit=raw.credit,
            captured_at=_as_aware(raw.captured_at),
            keywords=raw.keywords,
            raw_metadata=raw.raw_metadata,
            detail_url=raw.detail_url,
            thumbnail_path=storage.to_relative(files.thumbnail_path),
            preview_path=storage.to_relative(files.preview_path),
            file_bytes=files.file_bytes,
            downloaded_at=utcnow(),
        )
        session.add(asset)


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _summarise(result: RunResult) -> str:
    if result.status == "error":
        return result.message or "error"
    parts = [f"{result.new_assets} nuevas", f"{result.found} encontradas"]
    if result.purged_assets:
        parts.append(f"{result.purged_assets} purgadas")
    if result.errors:
        parts.append(f"{len(result.errors)} errores de descarga")
    return ", ".join(parts)


def run_all_enabled(limit: int = 100) -> list[RunResult]:
    """Run every enabled search once (used by the scheduler and CLI).

    El aviso de la agencia sale UNA vez por lote, no una por búsqueda, y la
    agencia se da por buena solo si TODAS sus búsquedas fueron bien. Es la otra
    mitad de la tormenta del 27-08-2026: el disparador por flanco de ``alerts``
    es por AGENCIA, y ``record_run`` se llamaba por BÚSQUEDA, así que con dos
    búsquedas de Reuters —una rota de forma persistente y otra sana— cada ciclo
    avisaba del fallo y acto seguido lo REARMABA con el éxito de la otra: un
    correo por ciclo de la misma avería continua. Agrupando aquí, el aviso vuelve
    a ser uno por avería sin tocar las claves del fichero de estado.
    """
    with session_scope() as session:
        busquedas = session.execute(
            select(Search.id, Search.agency).where(Search.enabled.is_(True))
        ).all()

    resultados: list[tuple[str, RunResult]] = []
    for sid, agency in busquedas:
        try:
            resultados.append((agency, run_search(sid, limit=limit, avisa=False)))
        except Exception as exc:  # noqa: BLE001
            # Un reventón que el runner no atrapa (la BD bloqueada al anotar el
            # RunLog, un hilo que no arranca) ni para el lote ni desaparece del
            # veredicto. Sin esta anotación, la agencia se juzgaba solo por las
            # búsquedas que llegaron a devolver algo: una que reventara así en
            # todos los ciclos quedaba en silencio eterno, y encima el éxito de
            # sus hermanas rearmaba el flanco — las dos mitades de lo que este
            # cambio vino a arreglar.
            log.exception("la búsqueda %s reventó fuera del runner", sid)
            resultados.append(
                (agency, RunResult(sid, "error", message=f"{type(exc).__name__}: {exc}"))
            )
    _avisa_del_lote(resultados)
    return [resultado for _, resultado in resultados]


def _avisa_del_lote(resultados: list[tuple[str, RunResult]]) -> None:
    """Manda a ``alerts`` un veredicto por agencia con lo que dio el lote entero.

    Si una sola de sus búsquedas falló, la agencia falló: rearmar el disparador
    con el éxito de una búsqueda hermana mientras otra sigue rota es exactamente
    lo que convertía «un aviso por avería» en «un aviso por ciclo».
    """
    settings = get_settings()
    por_agencia: dict[str, list[RunResult]] = {}
    for agency, resultado in resultados:
        por_agencia.setdefault(agency, []).append(resultado)

    for agency, suyos in por_agencia.items():
        # «search not found» es administración, no avería: la búsqueda se borró
        # desde el panel con el lote en marcha (la instantánea de ids se toma al
        # empezar). Con el aviso viejo por búsqueda, ese retorno temprano jamás
        # avisaba; convertir un borrado en «la agencia ha dejado de funcionar»
        # sería una regresión. Ni fallo ni éxito: fuera del veredicto.
        suyos = [r for r in suyos if r.message != "search not found"]
        if not suyos:
            continue
        fallidas = [r for r in suyos if r.status == "error"]
        try:
            alerts.record_run(
                settings, agency, not fallidas,
                fallidas[0].message if fallidas else None,
            )
        except Exception:  # noqa: BLE001 - el aviso es un extra; el lote ya corrió
            pass


def _avisa_de_un_run(settings, agency: str, result: RunResult) -> None:
    """El aviso del camino de UNA búsqueda (botón ↻, backfill, alta): solo el fallo.

    Este camino ve una sola búsqueda y el disparador de ``alerts`` es de la
    agencia entera: un éxito aquí rearmaba el flanco que una hermana rota
    acababa de disparar, y el siguiente lote repetía el correo de la misma
    avería — la variante manual de la tormenta del 27-08-2026. Rearmar es cosa
    del lote (:func:`_avisa_del_lote`), el único que ve a la agencia completa.
    El primer fallo, en cambio, debe salir también por aquí; los repetidos ya
    los silencia el flanco.
    """
    if result.status != "error":
        return
    try:
        alerts.record_run(settings, agency, False, result.message or None)
    except Exception:  # noqa: BLE001 - el aviso es un extra; el run ya está registrado
        pass
