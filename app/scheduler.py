"""Scheduler — el motor de refresco de la capa 1.

Modelo GLOBAL (desde 22-jul-2026): en vez de una cadencia por búsqueda, hay un
único job "refresh-all" que ejecuta TODAS las búsquedas activas juntas, cada
``refresh_every`` (horas o días) empezando a una hora fija (``refresh_start_*``).
Todo eso se configura desde Ajustes (modelo ``AppSettings``). Sigue existiendo
``run_now(search_id)`` para refrescar UNA búsqueda al instante desde la web.

Los jobs corren en un pool de hilos; el runner serializa el acceso a la BD y al
navegador con un lock, así que los disparos solapados son seguros.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from .database import session_scope
from .ingest.runner import run_all_enabled, run_search
from .models import AppSettings

log = logging.getLogger("scheduler")

REFRESH_JOB_ID = "refresh-all"
KEEPALIVE_JOB_ID = "reuters-keepalive"

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300}
        )
    return _scheduler


def start() -> None:
    sched = get_scheduler()
    if not sched.running:
        sched.start()
    reschedule()
    _schedule_keepalive()


def shutdown() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


# --- refresco global -------------------------------------------------------
def _run_all_job() -> None:
    try:
        results = run_all_enabled()
        oks = sum(1 for r in results if r.status != "error")
        log.info("refresh-all: %s búsquedas (%s ok)", len(results), oks)
    except Exception:  # noqa: BLE001
        log.exception("refresh-all crashed")


def reschedule() -> None:
    """(Re)programa el job global de refresco leyendo AppSettings."""
    with session_scope() as session:
        cfg = session.get(AppSettings, 1)
        every = cfg.refresh_every if cfg else 12
        unit = cfg.refresh_unit if cfg else "hours"
        hh = cfg.refresh_start_hour if cfg else 6
        mm = cfg.refresh_start_minute if cfg else 0

    interval = {"days": max(1, every)} if unit == "days" else {"hours": max(1, every)}
    sched = get_scheduler()
    sched.add_job(
        _run_all_job,
        trigger="interval",
        id=REFRESH_JOB_ID,
        # Ancla en HH:MM (naive/local; el contenedor va en Europe/Madrid). Si la
        # hora de hoy ya pasó, APScheduler proyecta el próximo disparo sobre la
        # rejilla (ancla + n·intervalo) sin disparar en el momento de programar.
        start_date=_anchor(hh, mm),
        replace_existing=True,
        **interval,
    )
    log.info("refresh-all reprogramado: %s", (cfg.refresh_summary() if cfg else "por defecto"))


def _anchor(hour: int, minute: int) -> datetime:
    now = datetime.now()
    return now.replace(
        hour=max(0, min(23, hour)), minute=max(0, min(59, minute)), second=0, microsecond=0
    )


# --- ejecución manual de UNA búsqueda --------------------------------------
def _run_job(search_id: int) -> None:
    try:
        result = run_search(search_id)
        log.info("search %s -> %s (%s)", search_id, result.status, result.message)
    except Exception:  # noqa: BLE001
        log.exception("search %s crashed", search_id)


def run_now(search_id: int) -> None:
    """Refresca una búsqueda al instante (botón ↻ de la web)."""
    sched = get_scheduler()
    sched.add_job(
        _run_job,
        trigger="date",
        run_date=datetime.now(timezone.utc),
        args=[search_id],
        id=f"run-now-{search_id}",
        replace_existing=True,
        misfire_grace_time=60,
    )


def _backfill_job(search_id: int) -> None:
    from .ingest.runner import backfill_search

    try:
        r = backfill_search(search_id)
        log.info("backfill %s -> %s (%s nuevas)", search_id, r.status, r.new_assets)
    except Exception:  # noqa: BLE001
        log.exception("backfill %s crashed", search_id)


def backfill_now(search_id: int) -> None:
    """Rellena el histórico de una búsqueda hasta su retención (botón «rellenar»)."""
    sched = get_scheduler()
    sched.add_job(
        _backfill_job,
        trigger="date",
        run_date=datetime.now(timezone.utc),
        args=[search_id],
        id=f"backfill-{search_id}",
        replace_existing=True,
        misfire_grace_time=120,
    )


def reuters_login_now(fase: str = "start") -> None:
    """Login manual de Reuters en dos pasos (botones de Ajustes).

    Va al pool de hilos porque puede esperar por el perfil: la petición web
    vuelve enseguida y el estado se consulta luego en la propia página.
    ``fase`` es "start" (abrir el navegador) o "check" (comprobar la sesión).
    """
    from .ingest import reuters_login

    trabajo = reuters_login.finish_login if fase == "check" else reuters_login.start_login
    sched = get_scheduler()
    sched.add_job(
        trabajo,
        trigger="date",
        run_date=datetime.now(timezone.utc),
        id=f"reuters-login-{fase}",
        replace_existing=True,
        misfire_grace_time=60,
    )


def run_all_now() -> None:
    """Refresca TODAS las búsquedas activas al instante (desde Ajustes)."""
    sched = get_scheduler()
    sched.add_job(
        _run_all_job,
        trigger="date",
        run_date=datetime.now(timezone.utc),
        id="refresh-all-now",
        replace_existing=True,
        misfire_grace_time=60,
    )


# --- keep-alive de la sesión de Reuters ------------------------------------
def _schedule_keepalive() -> None:
    from .config import get_settings

    sched = get_scheduler()
    mins = get_settings().reuters_keepalive_minutes
    existing = sched.get_job(KEEPALIVE_JOB_ID)
    if not mins or mins <= 0:
        if existing:
            existing.remove()
        return

    from .ingest.keepalive import keepalive_reuters

    sched.add_job(
        keepalive_reuters,
        trigger="interval",
        minutes=max(1, mins),
        start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        id=KEEPALIVE_JOB_ID,
        replace_existing=True,
    )
