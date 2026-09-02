"""Business-logic helpers used by the web layer.

Keeps the FastAPI route handlers thin: search CRUD (with scheduler
side-effects), dashboard statistics, and the activity feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import scheduler, storage
from .auth import hash_password
from .models import (
    GRUPO_POR_DEFECTO,
    KIND_PHOTOGRAPHER,
    KIND_TEXT,
    RETENTION_SIZE,
    RETENTION_TIME,
    AppSettings,
    Asset,
    RunLog,
    Search,
    SearchGroup,
    User,
    utcnow,
)

AGENCY_CHOICES = [
    ("ap", "Associated Press"),
    ("reuters", "Reuters"),
    ("afp", "AFP (vía Getty)"),
    ("getty", "Getty Images"),
]
KIND_CHOICES = [(KIND_PHOTOGRAPHER, "Fotógrafo"), (KIND_TEXT, "Búsqueda de texto")]
RETENTION_CHOICES = [(RETENTION_TIME, "Por tiempo (meses)"), (RETENTION_SIZE, "Por espacio (MB)")]


# --- create / update / delete --------------------------------------------
# Nota: el refresco es GLOBAL (un job que corre todas las búsquedas juntas,
# ver scheduler.py). Crear/editar/activar una búsqueda ya NO programa un job
# propio; enabled solo decide si el refresco global la incluye.
def create_search(
    session: Session, form: dict, user_id: int, *, primera_carga: bool = False
) -> Search:
    # Nace en «Sin grupo» y al final de él: es el único sitio que no exige
    # decidir nada al crearla, y arrastrarla a su grupo es un gesto. Preguntar el
    # grupo en el alta obligaría a inventarse uno antes de tener la primera
    # búsqueda, que es justo al revés de como se usa esto.
    grupo = grupo_por_defecto(session, user_id)
    search = Search(
        user_id=user_id,
        group_id=grupo.id,
        position=_siguiente_en_grupo(session, grupo.id),
        # Nace vista: sus primeras fotos no cuentan como "novedad desde tu
        # última visita" hasta que la búsqueda haya corrido estando tú fuera.
        seen_at=utcnow(),
        **_normalise_form(form),
    )
    session.add(search)
    session.commit()

    if primera_carga and search.enabled:
        # Una búsqueda recién creada está vacía, y esperar al refresco global
        # —que puede ser dentro de horas— para ver la primera foto no tiene
        # sentido: nadie crea una búsqueda para no mirarla. Se lanza ya, en
        # segundo plano, para no dejar colgada la petición del navegador; el
        # panel muestra mientras tanto su indicador de trabajo en curso.
        scheduler.run_now(search.id)
    return search


def update_search(session: Session, search: Search, form: dict) -> Search:
    for key, value in _normalise_form(form).items():
        setattr(search, key, value)
    session.commit()
    return search


def delete_search(session: Session, search: Search) -> None:
    # Remove media files for every asset before the cascade delete.
    for asset in search.assets:
        storage.remove_asset_files(asset.preview_path, asset.thumbnail_path)
    session.delete(search)
    session.commit()


def toggle_search(session: Session, search: Search) -> Search:
    search.enabled = not search.enabled
    session.commit()
    return search


def mark_seen(session: Session, search: Search) -> Search:
    """Apaga la luz de novedades de UNA búsqueda (al abrirla)."""
    search.seen_at = utcnow()
    session.commit()
    return search


def mark_asset_seen(session: Session, asset: Asset) -> Asset:
    """Marca UNA foto como vista, al abrirla a tamaño completo.

    Es definitivo: una foto que has mirado ya no vuelve a salir destacada,
    aunque sigas en la misma sesión del navegador.
    """
    if asset.seen_at is None:
        asset.seen_at = utcnow()
        session.commit()
    return asset


def ids_destacables(assets, frontera: datetime | None) -> set[int]:
    """De estas fotos, cuáles salen destacadas: las llegadas después de tu
    última visita y que aún no has abierto.

    ``frontera`` es el momento en que se quedó tu última visita a esta búsqueda.
    Con None (nunca visitada) no se destaca nada: encender la rejilla entera la
    primera vez no informa de nada.
    """
    if frontera is None:
        return set()
    limite = _aware(frontera)
    return {
        a.id
        for a in assets
        if a.seen_at is None and a.downloaded_at and _aware(a.downloaded_at) > limite
    }


# --- grupos y orden del panel ----------------------------------------------
# Cada búsqueda vive DENTRO de un grupo: el panel ordena por grupo y, dentro de
# cada uno, por la posición que fijó el usuario arrastrando.
def _siguiente_en_grupo(session: Session, group_id: int) -> int:
    """La posición libre al final de un grupo."""
    mayor = (
        session.scalar(select(func.max(Search.position)).where(Search.group_id == group_id)) or -1
    )
    return mayor + 1


def _next_group_position(session: Session, user_id: int) -> int:
    mayor = (
        session.scalar(
            select(func.max(SearchGroup.position)).where(SearchGroup.user_id == user_id)
        )
        or -1
    )
    return mayor + 1


def grupo_por_defecto(session: Session, user_id: int) -> SearchGroup:
    """El grupo «Sin grupo», creándolo si hace falta.

    Es el sitio de las búsquedas que no tienen otro: las recién creadas y las que
    se quedan sin casa al borrar su grupo. No es un grupo blindado —se renombra,
    se mueve y se borra como cualquiera—: simplemente vuelve a aparecer en cuanto
    alguna búsqueda lo necesita, que es lo que sostiene la regla de que ninguna
    se quede fuera.
    """
    grupo = session.scalars(
        select(SearchGroup).where(
            SearchGroup.user_id == user_id, SearchGroup.name == GRUPO_POR_DEFECTO
        )
    ).first()
    if grupo is None:
        grupo = SearchGroup(
            user_id=user_id,
            name=GRUPO_POR_DEFECTO,
            position=_next_group_position(session, user_id),
        )
        session.add(grupo)
        session.commit()
    return grupo


def create_group(session: Session, user_id: int, name: str = "") -> SearchGroup:
    grupo = SearchGroup(
        user_id=user_id,
        name=(name or "").strip(),
        position=_next_group_position(session, user_id),
    )
    session.add(grupo)
    session.commit()
    return grupo


def rename_group(session: Session, grupo: SearchGroup, name: str) -> SearchGroup:
    grupo.name = (name or "").strip()
    session.commit()
    return grupo


def delete_group(session: Session, grupo: SearchGroup) -> int:
    """Borra un grupo y **conserva sus búsquedas**, que pasan a «Sin grupo».

    Borrar la carpeta no puede borrar lo que hay dentro: son búsquedas con sus
    fotos en disco y su histórico, y quien borra un grupo está quitando una
    etiqueta, no tirando su trabajo. Para deshacerse de una búsqueda ya está su
    propio botón, que sí avisa de lo que se lleva por delante.

    Devuelve cuántas búsquedas se han mudado.
    """
    sueltas = list(
        session.scalars(
            select(Search).where(Search.group_id == grupo.id).order_by(Search.position, Search.id)
        )
    )
    if sueltas:
        destino = grupo_por_defecto(session, grupo.user_id)
        if destino.id == grupo.id:
            # Es el propio «Sin grupo» y tiene búsquedas dentro: no hay a dónde
            # mudarlas, así que no se borra. Vaciarlo primero y volver.
            return 0
        hueco = (
            session.scalar(select(func.max(Search.position)).where(Search.group_id == destino.id))
            or -1
        ) + 1
        for search in sueltas:
            # Por la RELACIÓN, no por la clave: al borrar el grupo, SQLAlchemy
            # recorre los hijos que aún tiene cargados en su colección y les pone
            # el group_id a NULL. Tocando solo la clave, ese barrido llegaba
            # DESPUÉS y deshacía la mudanza —las búsquedas acababan sin grupo, es
            # decir fuera del panel—. Moviéndolas de colección, ya no son suyas.
            search.group = destino
            search.position = hueco
            hueco += 1
        session.flush()
    session.delete(grupo)
    session.commit()
    return len(sueltas)


def own_group(session: Session, group_id: int, user_id: int) -> SearchGroup | None:
    grupo = session.get(SearchGroup, group_id)
    return grupo if grupo and grupo.user_id == user_id else None


def user_groups(session: Session, user_id: int) -> list[SearchGroup]:
    return list(
        session.scalars(
            select(SearchGroup)
            .where(SearchGroup.user_id == user_id)
            .order_by(SearchGroup.position, SearchGroup.id)
        )
    )


def reorder_panel(session: Session, user_id: int, items: list[dict]) -> int:
    """Reescribe el panel entero a partir de la lista del modo edición.

    ``items`` son ``{"type": "group"|"search", "id": N}`` en el orden en que han
    quedado en pantalla; los grupos pueden traer además ``label``, así que
    arrastrar y renombrar se guardan de una vez.

    **El grupo de cada búsqueda sale de la propia lista, no de un campo aparte**:
    una búsqueda pertenece al último grupo que aparece por encima de ella. Es lo
    que hace que arrastrar una fila de un bloque a otro la cambie de grupo sin
    más ceremonia —como mover una canción entre listas—, y evita el estado doble
    («el orden dice una cosa y el group_id otra») que luego habría que
    reconciliar.

    Una búsqueda que quede por encima del primer grupo cae en el primero de
    todos: en el panel no hay sitio fuera de un grupo, así que tampoco puede
    haberlo al guardar. Lo que no venga en la lista (una búsqueda creada en otra
    pestaña mientras ordenabas) se queda donde estaba.
    """
    mias = set(session.scalars(select(Search.id).where(Search.user_id == user_id)))
    mios = set(session.scalars(select(SearchGroup.id).where(SearchGroup.user_id == user_id)))

    vistos: list[SearchGroup] = []
    grupo_actual: SearchGroup | None = None
    dentro = 0
    tocadas = 0
    colocadas: set[int] = set()

    for item in items:
        tipo = str(item.get("type"))
        try:
            row_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue

        if tipo == "group":
            if row_id not in mios:
                continue
            grupo = session.get(SearchGroup, row_id)
            if grupo is None:
                continue
            grupo.position = len(vistos)
            if "label" in item:
                grupo.name = str(item.get("label") or "").strip()
            vistos.append(grupo)
            grupo_actual = grupo
            dentro = 0
            tocadas += 1

        elif tipo == "search":
            if row_id not in mias:
                continue
            search = session.get(Search, row_id)
            if search is None:
                continue
            if grupo_actual is None:
                grupo_actual = _primer_grupo(session, user_id, vistos)
                dentro = 0
            search.group_id = grupo_actual.id
            search.position = dentro
            dentro += 1
            tocadas += 1
            colocadas.add(search.id)

    # Los grupos que no venían en la lista van detrás, con su orden relativo.
    ids_vistos = {g.id for g in vistos}
    siguiente = len(vistos)
    for grupo in session.scalars(
        select(SearchGroup)
        .where(SearchGroup.user_id == user_id)
        .order_by(SearchGroup.position, SearchGroup.id)
    ):
        if grupo.id not in ids_vistos:
            grupo.position = siguiente
            siguiente += 1

    # Las búsquedas que no venían se quedan como estaban. Solo se les busca sitio
    # si además se habían quedado sin grupo (o con uno que ya no existe): la
    # regla «ninguna búsqueda fuera de un grupo» no puede romperse por guardar.
    for search in session.scalars(
        select(Search).where(Search.user_id == user_id).order_by(Search.position, Search.id)
    ):
        if search.id in colocadas or search.group_id in mios:
            continue
        destino = _primer_grupo(session, user_id, vistos)
        search.group_id = destino.id
        search.position = (
            session.scalar(select(func.max(Search.position)).where(Search.group_id == destino.id))
            or -1
        ) + 1

    session.commit()
    return tocadas


def _primer_grupo(session: Session, user_id: int, ya_vistos: list[SearchGroup]) -> SearchGroup:
    """El grupo que encabeza el panel, para lo que se quede sin sitio."""
    if ya_vistos:
        return ya_vistos[0]
    grupos = user_groups(session, user_id)
    return grupos[0] if grupos else grupo_por_defecto(session, user_id)


# --- ajustes globales ------------------------------------------------------
def get_app_settings(session: Session) -> AppSettings:
    cfg = session.get(AppSettings, 1)
    if cfg is None:  # por si la BD es anterior a esta tabla
        cfg = AppSettings(id=1)
        session.add(cfg)
        session.commit()
    return cfg


def update_app_settings(session: Session, form: dict) -> AppSettings:
    cfg = get_app_settings(session)
    cfg.photos_per_page = max(1, min(500, _to_int(form.get("photos_per_page"), cfg.photos_per_page)))
    cfg.refresh_every = max(1, _to_int(form.get("refresh_every"), cfg.refresh_every))
    unit = str(form.get("refresh_unit") or cfg.refresh_unit)
    cfg.refresh_unit = unit if unit in ("hours", "days") else "hours"
    cfg.refresh_start_hour = max(0, min(23, _to_int(form.get("refresh_start_hour"), cfg.refresh_start_hour)))
    cfg.refresh_start_minute = max(0, min(59, _to_int(form.get("refresh_start_minute"), cfg.refresh_start_minute)))
    session.commit()
    # Aplica el nuevo horario al job global de refresco.
    scheduler.reschedule()
    return cfg


def _to_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalise_form(form: dict) -> dict:
    mode = form.get("retention_mode", RETENTION_TIME)
    months = form.get("retention_months")
    mb = form.get("retention_mb")
    data = {
        "name": (form.get("name") or "").strip(),
        "agency": form["agency"],
        "kind": form["kind"],
        "query": (form.get("query") or "").strip(),
        "cadence_minutes": int(form.get("cadence_minutes") or 360),
        "retention_mode": mode,
        "retention_months": int(months) if (mode == RETENTION_TIME and months) else (int(months) if months else None),
        "retention_mb": int(mb) if (mode == RETENTION_SIZE and mb) else (int(mb) if mb else None),
        "enabled": _as_bool(form.get("enabled", True)),
    }
    # El grupo solo se toca si el formulario lo trae: el alta de una búsqueda no
    # lo pregunta (nace en «Sin grupo», y de ahí se arrastra a donde toque), y
    # meter aquí un None por omisión la dejaría fuera de todo grupo.
    grupo = form.get("group_id")
    if grupo not in (None, ""):
        data["group_id"] = int(grupo)
    if not data["name"]:
        data["name"] = f"{data['agency'].upper()} · {data['query']}"
    return data


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "on", "yes")


# --- statistics -----------------------------------------------------------
@dataclass
class SearchStats:
    search: Search
    asset_count: int
    total_bytes: int
    newest: datetime | None
    oldest: datetime | None
    # Cuándo se guardó la última foto. Comparado con el ``seen_at`` de la propia
    # búsqueda da ``has_new``: la luz es de cada búsqueda, no del panel entero.
    last_added: datetime | None = None
    has_new: bool = False
    # Hay una ejecución en curso ahora mismo (RunLog abierto): el panel muestra
    # un indicador en movimiento en esa fila.
    is_running: bool = False

    @property
    def total_mb(self) -> float:
        return round(self.total_bytes / (1024 * 1024), 1)


def search_stats(session: Session, search: Search) -> SearchStats:
    row = session.execute(
        select(
            func.count(Asset.id),
            func.coalesce(func.sum(Asset.file_bytes), 0),
            func.max(Asset.captured_at),
            func.min(Asset.captured_at),
            func.max(Asset.downloaded_at),
        ).where(Asset.search_id == search.id)
    ).one()
    stats = SearchStats(search, row[0], row[1], row[2], row[3], row[4])
    stats.has_new = _has_new(stats.last_added, search.seen_at)
    return stats


def _has_new(last_added: datetime | None, seen_at: datetime | None) -> bool:
    """¿Ha entrado alguna foto después de la última visita a esta búsqueda?"""
    if last_added is None:
        return False
    if seen_at is None:
        return True
    return _aware(last_added) > _aware(seen_at)


def _aware(value: datetime) -> datetime:
    """SQLite devuelve fechas sin zona; se leen siempre como UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def running_search_ids(session: Session, user_id: int) -> set[int]:
    """Búsquedas del usuario con una ejecución abierta ahora mismo.

    El runner deja un ``RunLog`` en estado ``running`` (sin ``finished_at``)
    mientras trabaja, y lo cierra al acabar. Se acota a las últimas horas por si
    un proceso muere sin cerrar el registro (el arranque además los sanea)."""
    from datetime import timedelta

    horizonte = utcnow() - timedelta(hours=2)
    mine = select(Search.id).where(Search.user_id == user_id)
    filas = session.execute(
        select(RunLog.search_id).where(
            RunLog.search_id.in_(mine),
            RunLog.status == "running",
            RunLog.finished_at.is_(None),
            RunLog.started_at > horizonte,
        )
    ).all()
    return {f[0] for f in filas}


def all_search_stats(session: Session, user_id: int) -> list[SearchStats]:
    searches = list(
        session.scalars(
            select(Search)
            .where(Search.user_id == user_id)
            .order_by(Search.position, Search.agency, Search.name)
        )
    )
    corriendo = running_search_ids(session, user_id)
    resultado = []
    for s in searches:
        st = search_stats(session, s)
        st.is_running = s.id in corriendo
        resultado.append(st)
    return resultado


@dataclass
class PanelBlock:
    """Un grupo del panel con las búsquedas que contiene, ya en su orden."""

    group: SearchGroup
    rows: list[SearchStats]

    @property
    def con_novedades(self) -> int:
        """Cuántas de sus búsquedas tienen la luz encendida.

        Va en la cabecera del grupo para que el corte entre bloques siga
        diciendo algo de un vistazo aunque el bloque esté plegado en la mirada:
        el panel se lee por grupos, no fila a fila.
        """
        return sum(1 for st in self.rows if st.has_new)


def panel_blocks(session: Session, user_id: int) -> list[PanelBlock]:
    """El panel entero: grupos en su orden, y dentro cada búsqueda en el suyo.

    Se garantiza aquí —y no solo en el arranque— que ninguna búsqueda queda
    fuera: si aparece alguna sin grupo (una base tocada a mano, un grupo borrado
    desde fuera), se adopta al vuelo en «Sin grupo» en lugar de desaparecer del
    panel, que es lo que pasaría al agrupar por un ``group_id`` nulo.
    """
    huerfanas = list(
        session.scalars(
            select(Search).where(Search.user_id == user_id, Search.group_id.is_(None))
        )
    )
    if huerfanas:
        destino = grupo_por_defecto(session, user_id)
        hueco = (
            session.scalar(select(func.max(Search.position)).where(Search.group_id == destino.id))
            or -1
        ) + 1
        for search in huerfanas:
            search.group_id = destino.id
            search.position = hueco
            hueco += 1
        session.commit()

    por_grupo: dict[int, list[SearchStats]] = {}
    for st in all_search_stats(session, user_id):
        por_grupo.setdefault(st.search.group_id, []).append(st)

    return [
        PanelBlock(group=grupo, rows=por_grupo.get(grupo.id, []))
        for grupo in user_groups(session, user_id)
    ]


@dataclass
class GlobalStats:
    searches: int
    enabled: int
    assets: int
    total_mb: float
    agencies: dict[str, int]


def global_stats(session: Session, user_id: int) -> GlobalStats:
    """Estadísticas del usuario (cada uno ve solo lo suyo)."""
    mine = Search.user_id == user_id
    total_searches = session.scalar(select(func.count(Search.id)).where(mine)) or 0
    enabled = (
        session.scalar(select(func.count(Search.id)).where(mine, Search.enabled.is_(True))) or 0
    )
    asset_join = select(Asset).join(Search, Asset.search_id == Search.id).where(mine)
    total_assets = session.scalar(
        select(func.count()).select_from(asset_join.subquery())
    ) or 0
    total_bytes = session.scalar(
        select(func.coalesce(func.sum(Asset.file_bytes), 0))
        .select_from(Asset)
        .join(Search, Asset.search_id == Search.id)
        .where(mine)
    ) or 0
    by_agency = dict(
        session.execute(
            select(Asset.agency, func.count(Asset.id))
            .join(Search, Asset.search_id == Search.id)
            .where(mine)
            .group_by(Asset.agency)
        ).all()
    )
    return GlobalStats(
        searches=total_searches,
        enabled=enabled,
        assets=total_assets,
        total_mb=round(total_bytes / (1024 * 1024), 1),
        agencies=by_agency,
    )


# --- per-search photo view -------------------------------------------------
def _chrono_order():
    """Orden de la vista de búsqueda: más nueva primero (fallback: descarga)."""
    return (func.coalesce(Asset.captured_at, Asset.downloaded_at).desc(), Asset.id.desc())


def search_assets(
    session: Session, search_id: int, page: int = 1, per_page: int = 60
) -> tuple[list[Asset], int]:
    total = session.scalar(select(func.count(Asset.id)).where(Asset.search_id == search_id)) or 0
    stmt = (
        select(Asset)
        .where(Asset.search_id == search_id)
        .order_by(*_chrono_order())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    return list(session.scalars(stmt)), total


def group_search_ids(session: Session, group_id: int) -> list[int]:
    return list(session.scalars(select(Search.id).where(Search.group_id == group_id)))


def group_search_stats(session: Session, group_id: int, user_id: int) -> list[SearchStats]:
    """Las búsquedas del grupo con sus datos, en el orden del panel.

    La cabecera del feed las lista para que se vea de qué está hecho lo que se
    está mirando: un feed mezclado sin decir de dónde sale es un montón de fotos
    sin procedencia.
    """
    return [
        st for st in all_search_stats(session, user_id) if st.search.group_id == group_id
    ]


def group_assets(
    session: Session, group_id: int, page: int = 1, per_page: int = 60
) -> tuple[list[Asset], int]:
    """El feed de un grupo: las fotos de TODAS sus búsquedas, de nueva a vieja.

    Es la vista que hace que un grupo valga para algo más que ordenar el panel:
    en vez de entrar búsqueda por búsqueda para ver qué ha caído, se lee el
    bloque entero de corrido, mezclado y en orden cronológico —que es como se
    mira el trabajo de un equipo—.

    Mismo orden que la vista de una búsqueda (``_chrono_order``): por fecha de
    toma y, si falta, por la de descarga. Deliberadamente el mismo, para que
    pasar de una vista a otra no reordene las fotos bajo los pies.
    """
    ids = group_search_ids(session, group_id)
    if not ids:
        return [], 0
    total = session.scalar(select(func.count(Asset.id)).where(Asset.search_id.in_(ids))) or 0
    stmt = (
        select(Asset)
        .where(Asset.search_id.in_(ids))
        .order_by(*_chrono_order())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    return list(session.scalars(stmt)), total


def group_destacadas(session: Session, assets, group_id: int) -> set[int]:
    """Qué fotos del feed salen destacadas.

    La frontera es la de CADA búsqueda (su ``seen_at``), no una del grupo: la luz
    de novedades sigue siendo de cada búsqueda, y el feed no la apaga —abrirlo es
    leer, no dar por vistas quince búsquedas de golpe—. Así una foto se destaca
    aquí exactamente igual que se destacaría dentro de su búsqueda.
    """
    fronteras = {
        s.id: s.seen_at
        for s in session.scalars(select(Search).where(Search.group_id == group_id))
    }
    destacadas: set[int] = set()
    for asset in assets:
        frontera = fronteras.get(asset.search_id)
        if frontera is None:
            continue
        if asset.seen_at is None and asset.downloaded_at:
            if _aware(asset.downloaded_at) > _aware(frontera):
                destacadas.add(asset.id)
    return destacadas


def adjacent_asset_ids(session: Session, asset: Asset) -> tuple[int | None, int | None]:
    """IDs (anterior, siguiente) respecto al orden cronológico de su búsqueda."""
    ids = list(
        session.scalars(
            select(Asset.id).where(Asset.search_id == asset.search_id).order_by(*_chrono_order())
        )
    )
    i = ids.index(asset.id)
    prev_id = ids[i - 1] if i > 0 else None
    next_id = ids[i + 1] if i + 1 < len(ids) else None
    return prev_id, next_id


# --- activity feed --------------------------------------------------------
def recent_runs(session: Session, user_id: int, limit: int = 20) -> list[RunLog]:
    """Ejecuciones de las búsquedas del usuario (las huérfanas no se listan)."""
    mine = select(Search.id).where(Search.user_id == user_id)
    return list(
        session.scalars(
            select(RunLog)
            .where(RunLog.search_id.in_(mine))
            .order_by(RunLog.started_at.desc())
            .limit(limit)
        )
    )


# --- la cuenta (desde la 1.1 hay una sola) ---------------------------------
def update_user(session: Session, user: User, username: str, password: str) -> User:
    """Cambia el nombre y/o la contraseña de la única cuenta."""
    username = (username or "").strip()
    if username and username != user.username:
        if not session.scalar(select(User).where(User.username == username)):
            user.username = username
    if password:
        user.password_hash = hash_password(password)
    session.commit()
    return user
