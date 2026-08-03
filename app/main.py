"""FastAPI application: newsphotostalker 1.1 — panel + API, un solo usuario.

En producción no se expone directamente: corre detrás de nginx en un socket
unix (ver ``deploy/``). nginx sirve ``/static`` y ``/media`` como ficheros y
solo lo dinámico llega aquí.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import auth, scheduler, services
from .config import BUNDLE_DIR, get_settings
from .database import get_db, init_db
from .models import Asset, Search, User

templates = Jinja2Templates(directory=str(BUNDLE_DIR / "app" / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="newsphotostalker", lifespan=lifespan)

settings = get_settings()
app.mount("/static", StaticFiles(directory=str(BUNDLE_DIR / "app" / "static")), name="static")
app.mount("/media", StaticFiles(directory=str(settings.media_dir)), name="media")


# --- autenticación ---------------------------------------------------------
class NotAuthenticated(Exception):
    """Sin sesión válida → a /login."""


@app.exception_handler(NotAuthenticated)
async def _to_login(request: Request, exc: NotAuthenticated):
    return RedirectResponse("/login", status_code=303)


def current_user(request: Request, db: Session) -> User | None:
    token = request.cookies.get(auth.COOKIE_NAME)
    uid = auth.verify_token(token) if token else None
    return db.get(User, uid) if uid else None


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user(request, db)
    if user is None:
        raise NotAuthenticated()
    return user


def _own_search(db: Session, search_id: int, user: User) -> Search | None:
    search = db.get(Search, search_id)
    if search is None or search.user_id != user.id:
        return None
    return search


# --- template helpers -----------------------------------------------------
def _humanize_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - value
    secs = int(delta.total_seconds())
    if secs < 60:
        return "hace un momento"
    if secs < 3600:
        return f"hace {secs // 60} min"
    if secs < 86400:
        return f"hace {secs // 3600} h"
    return value.strftime("%Y-%m-%d %H:%M")


_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
_DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _dateline() -> str:
    """Fecha de la cabecera, al estilo del encabezado de un despacho."""
    hoy = datetime.now()
    return f"{_DIAS[hoy.weekday()]} {hoy.day} {_MESES[hoy.month - 1]} {hoy.year}"


def _short_dt(value: datetime | None) -> str:
    """Fecha corta en castellano: «2 ago, 14:35» (con el año si no es de este)."""
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    stamp = f"{value.day} {_MESES[value.month - 1]}"
    if value.year != datetime.now(timezone.utc).year:
        return f"{stamp} {value.year}"
    return f"{stamp}, {value:%H:%M}"


templates.env.filters["humanize"] = _humanize_dt
templates.env.filters["corta"] = _short_dt
templates.env.filters["ymd"] = lambda v: v.strftime("%Y-%m-%d") if v else "—"
templates.env.globals["settings"] = settings


def _ctx(request: Request, **kwargs) -> dict:
    base = {
        "request": request,
        "agency_choices": services.AGENCY_CHOICES,
        "kind_choices": services.KIND_CHOICES,
        "retention_choices": services.RETENTION_CHOICES,
        "hoy": _dateline(),
        "user": None,
    }
    base.update(kwargs)
    return base


# --- login / logout --------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, db: Session = Depends(get_db), error: int = 0):
    if current_user(request, db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", _ctx(request, error=error))


@app.post("/login")
def login(
    db: Session = Depends(get_db),
    username: str = Form(""),
    password: str = Form(""),
    remember: str = Form("off"),
):
    from sqlalchemy import select

    user = db.scalar(select(User).where(User.username == username.strip()))
    if not user or not auth.verify_password(password, user.password_hash):
        return RedirectResponse("/login?error=1", status_code=303)

    remember_me = str(remember).lower() in ("1", "true", "on", "yes")
    seconds = (auth.REMEMBER_DAYS * 86400) if remember_me else (auth.SESSION_HOURS * 3600)
    token = auth.make_token(user.id, seconds=seconds)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        auth.COOKIE_NAME,
        token,
        # Sin "recordar": cookie de sesión (muere al cerrar el navegador).
        max_age=seconds if remember_me else None,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME)
    return response


# --- panel ------------------------------------------------------------------
# La luz de novedades es de cada búsqueda y vive en la base de datos
# (``Search.seen_at``), no en una cookie del navegador: abrir una búsqueda apaga
# solo la suya y el estado sobrevive a cambiar de navegador o borrar cookies.
@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    edit: int | None = None,
    order: int = 0,
):
    return templates.TemplateResponse(
        "dashboard.html",
        _ctx(
            request,
            rows=services.panel_rows(db, user.id),
            edit_id=edit,
            order_mode=bool(order),
            user=user,
        ),
    )


@app.get("/searches/new", response_class=HTMLResponse)
def new_search_form(request: Request, user: User = Depends(require_user)):
    return templates.TemplateResponse(
        "search_form.html",
        _ctx(request, search=None, default_cadence=settings.default_cadence_minutes, user=user),
    )


@app.post("/searches")
def create_search(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    name: str = Form(""),
    agency: str = Form(...),
    kind: str = Form(...),
    query: str = Form(...),
    cadence_minutes: int = Form(360),
    retention_mode: str = Form("time"),
    retention_months: str = Form(""),
    retention_mb: str = Form(""),
    enabled: str = Form("on"),
):
    services.create_search(db, locals_to_form(locals()), user.id)
    return RedirectResponse("/", status_code=303)


@app.get("/searches/{search_id}", response_class=HTMLResponse)
def search_view(
    search_id: int,
    request: Request,
    page: int = 1,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    search = _own_search(db, search_id, user)
    if not search:
        return RedirectResponse("/", status_code=303)
    # Abrir la búsqueda es "ver las novedades": apaga SOLO esta luz.
    services.mark_seen(db, search)
    per_page = services.get_app_settings(db).photos_per_page
    assets, total = services.search_assets(db, search_id, page=page, per_page=per_page)
    return templates.TemplateResponse(
        "search_view.html",
        _ctx(
            request,
            search=search,
            stats=services.search_stats(db, search),
            assets=assets,
            total=total,
            page=page,
            per_page=per_page,
            user=user,
        ),
    )


@app.post("/searches/{search_id}")
def update_search(
    search_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    name: str = Form(""),
    agency: str = Form(...),
    kind: str = Form(...),
    query: str = Form(...),
    cadence_minutes: int = Form(360),
    retention_mode: str = Form("time"),
    retention_months: str = Form(""),
    retention_mb: str = Form(""),
    enabled: str = Form("off"),
):
    search = _own_search(db, search_id, user)
    if search:
        services.update_search(db, search, locals_to_form(locals()))
    return RedirectResponse("/", status_code=303)


@app.post("/searches/{search_id}/delete")
def delete_search(
    search_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    search = _own_search(db, search_id, user)
    if search:
        services.delete_search(db, search)
    return RedirectResponse("/", status_code=303)


@app.post("/searches/{search_id}/toggle")
def toggle_search(
    search_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    search = _own_search(db, search_id, user)
    if search:
        services.toggle_search(db, search)
    return RedirectResponse("/", status_code=303)


@app.post("/searches/{search_id}/run")
def run_search_now(
    search_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    if _own_search(db, search_id, user):
        scheduler.run_now(search_id)
    return RedirectResponse("/", status_code=303)


@app.post("/searches/{search_id}/backfill")
def backfill_search_now(
    search_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Rellena el histórico de la búsqueda hasta su límite de retención (en 2º plano)."""
    if _own_search(db, search_id, user):
        scheduler.backfill_now(search_id)
    return RedirectResponse(f"/searches/{search_id}", status_code=303)


# --- orden del panel y separadores -----------------------------------------
@app.post("/panel/order")
async def save_panel_order(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Guarda el orden que dejó el modo edición (búsquedas y separadores)."""
    payload = await request.json()
    items = payload.get("items") if isinstance(payload, dict) else payload
    moved = services.reorder_panel(db, user.id, items if isinstance(items, list) else [])
    return JSONResponse({"ok": True, "moved": moved})


@app.post("/separators")
def add_separator(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    label: str = Form(""),
):
    services.create_separator(db, user.id, label)
    return RedirectResponse("/?order=1", status_code=303)


@app.post("/separators/{separator_id}")
def rename_separator(
    separator_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    label: str = Form(""),
):
    separator = services.own_separator(db, separator_id, user.id)
    if separator:
        services.update_separator(db, separator, label)
    return RedirectResponse("/?order=1", status_code=303)


@app.post("/separators/{separator_id}/delete")
def remove_separator(
    separator_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    separator = services.own_separator(db, separator_id, user.id)
    if separator:
        services.delete_separator(db, separator)
    return RedirectResponse("/?order=1", status_code=303)


@app.get("/asset/{asset_id}", response_class=HTMLResponse)
def asset_detail(
    asset_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    asset = db.get(Asset, asset_id)
    if not asset or not _own_search(db, asset.search_id, user):
        return RedirectResponse("/", status_code=303)
    prev_id, next_id = services.adjacent_asset_ids(db, asset)
    return templates.TemplateResponse(
        "asset.html", _ctx(request, asset=asset, prev_id=prev_id, next_id=next_id, user=user)
    )


@app.get("/activity", response_class=HTMLResponse)
def activity(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    runs = services.recent_runs(db, user.id, limit=100)
    search_names = {s.id: s.name for s in user.searches}
    return templates.TemplateResponse(
        "activity.html",
        _ctx(request, runs=runs, search_names=search_names, user=user),
    )


# --- ajustes ---------------------------------------------------------------
@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    creds = {}
    for agency in ["ap", "reuters", "getty"]:
        c = settings.agencies.get(agency)
        creds[agency] = {
            "enabled": c.enabled if c else False,
            "has_login": c.has_login if c else False,
            "username": (c.username if c else None),
        }
    from .ingest.reuters_login import STATUS as reuters_login_status

    perfil = settings.data_dir / "browser" / "reuters"
    return templates.TemplateResponse(
        "settings.html",
        _ctx(
            request,
            creds=creds,
            gstats=services.global_stats(db, user.id),
            app_settings=services.get_app_settings(db),
            # Solo dice si hay perfil de navegador guardado, no si la sesión
            # sigue siendo válida: eso únicamente se sabe ejecutando.
            reuters_profile=perfil.exists(),
            reuters_login=reuters_login_status,
            data_dir=str(settings.data_dir),
            user=user,
        ),
    )


@app.post("/settings/global")
def update_global_settings(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    photos_per_page: str = Form(""),
    refresh_every: str = Form(""),
    refresh_unit: str = Form("hours"),
    refresh_start_hour: str = Form(""),
    refresh_start_minute: str = Form("0"),
):
    services.update_app_settings(db, {
        "photos_per_page": photos_per_page,
        "refresh_every": refresh_every,
        "refresh_unit": refresh_unit,
        "refresh_start_hour": refresh_start_hour,
        "refresh_start_minute": refresh_start_minute,
    })
    return RedirectResponse("/settings", status_code=303)


@app.post("/refresh-now")
def refresh_now_all(user: User = Depends(require_user)):
    scheduler.run_all_now()
    return RedirectResponse("/settings", status_code=303)


@app.post("/reuters/login")
def reuters_login(user: User = Depends(require_user)):
    """Abre la ventana del login manual de Reuters (en segundo plano).

    Es la única ventana que enseña el programa, y con esto ya no hace falta un
    script aparte: la versión empaquetada se maneja entera desde el panel.
    """
    scheduler.reuters_login_now()
    return RedirectResponse("/settings", status_code=303)


# --- la cuenta (una sola desde la 1.1) -------------------------------------
@app.post("/account")
def update_account(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    username: str = Form(""),
    password: str = Form(""),
):
    services.update_user(db, user, username, password)
    return RedirectResponse("/settings", status_code=303)


# --- JSON API (con sesión) -------------------------------------------------
@app.get("/api/status")
def api_status(db: Session = Depends(get_db), user: User = Depends(require_user)):
    g = services.global_stats(db, user.id)
    return JSONResponse(
        {
            "user": user.username,
            "searches": g.searches,
            "enabled": g.enabled,
            "assets": g.assets,
            "total_mb": g.total_mb,
            "by_agency": g.agencies,
            "jobs": _scheduler_jobs(),
        }
    )


@app.get("/api/searches")
def api_searches(db: Session = Depends(get_db), user: User = Depends(require_user)):
    out = []
    for st in services.all_search_stats(db, user.id):
        s = st.search
        out.append(
            {
                "id": s.id,
                "name": s.name,
                "agency": s.agency,
                "kind": s.kind,
                "query": s.query,
                "cadence_minutes": s.cadence_minutes,
                "retention": s.retention_summary(),
                "enabled": s.enabled,
                "assets": st.asset_count,
                "mb": st.total_mb,
                "last_run": s.last_run_at.isoformat() if s.last_run_at else None,
                "last_status": s.last_status,
            }
        )
    return JSONResponse(out)


def _scheduler_jobs() -> list[dict]:
    jobs = []
    for job in scheduler.get_scheduler().get_jobs():
        jobs.append(
            {
                "id": job.id,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            }
        )
    return jobs


def locals_to_form(values: dict) -> dict:
    keys = [
        "name",
        "agency",
        "kind",
        "query",
        "cadence_minutes",
        "retention_mode",
        "retention_months",
        "retention_mb",
        "enabled",
    ]
    return {k: values[k] for k in keys}
