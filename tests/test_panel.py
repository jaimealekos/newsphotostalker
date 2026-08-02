"""Panel: luz de novedades por búsqueda, orden y separadores.

Contra una base en memoria, sin tocar el servidor web: lo que se prueba aquí es
la regla de negocio, no la plantilla.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import services
from app.database import Base
from app.models import Asset, Search, Separator, User


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture()
def user(session):
    u = User(username="yo", password_hash="x")
    session.add(u)
    session.flush()
    return u


def _search(session, user, name, **kw):
    defaults = dict(name=name, agency="ap", kind="text", query=name, user_id=user.id)
    defaults.update(kw)
    search = Search(**defaults)
    session.add(search)
    session.flush()
    return search


def _add_photo(session, search, *, hours_ago=1):
    now = datetime.now(timezone.utc)
    asset = Asset(
        search_id=search.id,
        agency=search.agency,
        external_id=f"{search.id}-{hours_ago}",
        captured_at=now - timedelta(hours=hours_ago),
        downloaded_at=now - timedelta(hours=hours_ago),
        file_bytes=1024,
    )
    session.add(asset)
    session.flush()
    return asset


# --- luz de novedades ------------------------------------------------------
def test_sin_fotos_no_hay_luz(session, user):
    search = _search(session, user, "vacía")
    assert services.search_stats(session, search).has_new is False


def test_la_luz_se_enciende_con_fotos_posteriores_a_la_visita(session, user):
    ayer = datetime.now(timezone.utc) - timedelta(days=1)
    search = _search(session, user, "con novedades", seen_at=ayer)
    _add_photo(session, search, hours_ago=2)
    assert services.search_stats(session, search).has_new is True


def test_la_luz_se_apaga_solo_en_la_busqueda_visitada(session, user):
    """El fallo de la 1.0: una sola marca de visita apagaba todas las luces."""
    ayer = datetime.now(timezone.utc) - timedelta(days=1)
    searches = [_search(session, user, f"b{i}", seen_at=ayer) for i in range(4)]
    for search in searches:
        _add_photo(session, search, hours_ago=2)
    session.commit()

    assert [st.has_new for st in services.all_search_stats(session, user.id)] == [True] * 4

    services.mark_seen(session, searches[1])

    encendidas = [st.has_new for st in services.all_search_stats(session, user.id)]
    assert encendidas == [True, False, True, True]


def test_ver_el_panel_no_apaga_nada(session, user):
    """Listar el panel es de solo lectura: solo abrir la búsqueda apaga."""
    ayer = datetime.now(timezone.utc) - timedelta(days=1)
    search = _search(session, user, "b", seen_at=ayer)
    _add_photo(session, search, hours_ago=2)

    services.all_search_stats(session, user.id)
    services.panel_rows(session, user.id)

    assert search.seen_at == ayer
    assert services.search_stats(session, search).has_new is True


def test_una_busqueda_nueva_nace_vista(session, user):
    search = services.create_search(
        session, {"name": "recién creada", "agency": "ap", "kind": "text", "query": "q"}, user.id
    )
    assert search.seen_at is not None
    assert services.search_stats(session, search).has_new is False


# --- orden y separadores ---------------------------------------------------
def test_el_panel_mezcla_busquedas_y_separadores_en_orden(session, user):
    a = _search(session, user, "a", position=2)
    b = _search(session, user, "b", position=0)
    sep = Separator(user_id=user.id, label="Deportes", position=1)
    session.add(sep)
    session.commit()

    rows = services.panel_rows(session, user.id)
    assert [r.kind for r in rows] == ["search", "separator", "search"]
    assert rows[0].stats.search.id == b.id
    assert rows[1].separator.label == "Deportes"
    assert rows[2].stats.search.id == a.id


def test_reordenar_guarda_orden_y_titulo_del_separador(session, user):
    primera = _search(session, user, "primera", position=0)
    segunda = _search(session, user, "segunda", position=1)
    sep = services.create_separator(session, user.id, "sin nombre")

    services.reorder_panel(
        session,
        user.id,
        [
            {"type": "separator", "id": sep.id, "label": "Agencias"},
            {"type": "search", "id": segunda.id},
            {"type": "search", "id": primera.id},
        ],
    )

    rows = services.panel_rows(session, user.id)
    assert [r.kind for r in rows] == ["separator", "search", "search"]
    assert rows[0].separator.label == "Agencias"
    assert [r.stats.search.name for r in rows[1:]] == ["segunda", "primera"]


def test_reordenar_deja_al_final_lo_que_no_venia_en_la_lista(session, user):
    """Una búsqueda creada en otra pestaña mientras ordenabas no se cuela arriba."""
    a = _search(session, user, "a", position=0)
    b = _search(session, user, "b", position=1)
    fuera = _search(session, user, "creada aparte", position=2)

    services.reorder_panel(
        session, user.id, [{"type": "search", "id": b.id}, {"type": "search", "id": a.id}]
    )

    assert [r.stats.search.name for r in services.panel_rows(session, user.id)] == [
        "b",
        "a",
        "creada aparte",
    ]


def test_reordenar_ignora_filas_de_otro_dueno_y_basura(session, user):
    otro = User(username="otro", password_hash="x")
    session.add(otro)
    session.flush()
    ajena = _search(session, otro, "ajena", position=0)
    mia = _search(session, user, "mía", position=5)

    services.reorder_panel(
        session,
        user.id,
        [
            {"type": "search", "id": ajena.id},
            {"type": "carpeta", "id": 1},
            {"type": "search", "id": "no-es-un-numero"},
            {"type": "search", "id": mia.id},
        ],
    )

    assert ajena.position == 0  # intacta
    assert [r.stats.search.name for r in services.panel_rows(session, user.id)] == ["mía"]
