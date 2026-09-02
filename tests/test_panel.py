"""Panel: luz de novedades por búsqueda, grupos y orden.

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
from app.models import Asset, Search, User, utcnow


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
    services.panel_blocks(session, user.id)

    # _aware: SQLite devuelve las fechas sin zona, y panel_blocks pasa por la
    # base al adoptar la búsqueda huérfana. Lo que se comprueba es el INSTANTE.
    assert services._aware(search.seen_at) == ayer
    assert services.search_stats(session, search).has_new is True


def test_una_busqueda_nueva_nace_vista(session, user):
    search = services.create_search(
        session, {"name": "recién creada", "agency": "ap", "kind": "text", "query": "q"}, user.id
    )
    assert search.seen_at is not None
    assert services.search_stats(session, search).has_new is False


# --- grupos ----------------------------------------------------------------
def _nombres(session, user):
    """El panel aplanado: ['GRUPO', 'búsqueda', 'búsqueda', 'OTRO', ...]."""
    salida = []
    for block in services.panel_blocks(session, user.id):
        salida.append(block.group.name.upper())
        salida += [st.search.name for st in block.rows]
    return salida


def test_ninguna_busqueda_se_queda_fuera_de_un_grupo(session, user):
    """La regla que sostiene todo lo demás.

    Una búsqueda sin grupo no es un caso raro de laboratorio: es lo que hay en
    cuanto se actualiza desde la 1.2 (la columna nace vacía). Si el panel
    agrupara por ``group_id`` a secas, esas búsquedas no saldrían en ninguna
    parte — desaparecerían de la pantalla sin haberse borrado.
    """
    _search(session, user, "huérfana")
    session.commit()

    bloques = services.panel_blocks(session, user.id)

    assert [b.group.name for b in bloques] == ["Sin grupo"]
    assert [st.search.name for st in bloques[0].rows] == ["huérfana"]


def test_una_busqueda_nueva_nace_en_sin_grupo(session, user):
    search = services.create_search(
        session, {"name": "recién creada", "agency": "ap", "kind": "text", "query": "q"}, user.id
    )
    assert search.group_id == services.grupo_por_defecto(session, user.id).id


def test_el_panel_va_por_grupos_y_dentro_por_posicion(session, user):
    deportes = services.create_group(session, user.id, "Deportes")
    guerra = services.create_group(session, user.id, "Guerra")
    _search(session, user, "b", group_id=deportes.id, position=1)
    _search(session, user, "a", group_id=deportes.id, position=0)
    _search(session, user, "c", group_id=guerra.id, position=0)
    session.commit()

    assert _nombres(session, user) == ["DEPORTES", "a", "b", "GUERRA", "c"]


def test_arrastrar_una_busqueda_a_otro_grupo_la_cambia_de_grupo(session, user):
    """El gesto de la lista de reproducción: se suelta en otro bloque y ya está.

    El grupo NO viaja como un campo suyo: sale de dónde ha quedado la fila. Por
    eso basta con mandar la lista tal cual quedó en pantalla.
    """
    uno = services.create_group(session, user.id, "Uno")
    dos = services.create_group(session, user.id, "Dos")
    viajera = _search(session, user, "viajera", group_id=uno.id, position=0)
    quieta = _search(session, user, "quieta", group_id=uno.id, position=1)
    session.commit()

    services.reorder_panel(
        session,
        user.id,
        [
            {"type": "group", "id": uno.id},
            {"type": "search", "id": quieta.id},
            {"type": "group", "id": dos.id},
            {"type": "search", "id": viajera.id},
        ],
    )

    assert viajera.group_id == dos.id
    assert quieta.group_id == uno.id
    assert _nombres(session, user) == ["UNO", "quieta", "DOS", "viajera"]


def test_reordenar_guarda_el_nombre_del_grupo_a_la_vez(session, user):
    grupo = services.create_group(session, user.id, "sin nombre")
    primera = _search(session, user, "primera", group_id=grupo.id, position=0)
    segunda = _search(session, user, "segunda", group_id=grupo.id, position=1)
    session.commit()

    services.reorder_panel(
        session,
        user.id,
        [
            {"type": "group", "id": grupo.id, "label": "Agencias"},
            {"type": "search", "id": segunda.id},
            {"type": "search", "id": primera.id},
        ],
    )

    assert _nombres(session, user) == ["AGENCIAS", "segunda", "primera"]


def test_una_busqueda_por_encima_del_primer_grupo_cae_dentro_de_el(session, user):
    """En el panel no hay sitio fuera de un grupo, así que tampoco al guardar."""
    grupo = services.create_group(session, user.id, "Único")
    suelta = _search(session, user, "suelta", group_id=grupo.id, position=0)
    session.commit()

    services.reorder_panel(
        session,
        user.id,
        [{"type": "search", "id": suelta.id}, {"type": "group", "id": grupo.id}],
    )

    assert suelta.group_id == grupo.id


def test_reordenar_no_toca_lo_que_no_venia_en_la_lista(session, user):
    """Una búsqueda creada en otra pestaña mientras ordenabas no se mueve."""
    grupo = services.create_group(session, user.id, "G")
    a = _search(session, user, "a", group_id=grupo.id, position=0)
    b = _search(session, user, "b", group_id=grupo.id, position=1)
    fuera = _search(session, user, "creada aparte", group_id=grupo.id, position=2)
    session.commit()

    services.reorder_panel(
        session,
        user.id,
        [
            {"type": "group", "id": grupo.id},
            {"type": "search", "id": b.id},
            {"type": "search", "id": a.id},
        ],
    )

    assert fuera.group_id == grupo.id
    assert _nombres(session, user) == ["G", "b", "a", "creada aparte"]


def test_reordenar_ignora_filas_de_otro_dueno_y_basura(session, user):
    otro = User(username="otro", password_hash="x")
    session.add(otro)
    session.flush()
    suyo = services.create_group(session, otro.id, "Ajeno")
    ajena = _search(session, otro, "ajena", group_id=suyo.id, position=0)
    grupo = services.create_group(session, user.id, "Mío")
    mia = _search(session, user, "mía", group_id=grupo.id, position=5)
    session.commit()

    services.reorder_panel(
        session,
        user.id,
        [
            {"type": "group", "id": suyo.id},
            {"type": "search", "id": ajena.id},
            {"type": "carpeta", "id": 1},
            {"type": "search", "id": "no-es-un-numero"},
            {"type": "group", "id": grupo.id},
            {"type": "search", "id": mia.id},
        ],
    )

    assert ajena.group_id == suyo.id  # intacta
    assert ajena.position == 0
    assert _nombres(session, user) == ["MÍO", "mía"]


def test_borrar_un_grupo_conserva_sus_busquedas(session, user):
    """Borrar la carpeta no puede borrar lo que hay dentro.

    Son búsquedas con sus fotos en disco y su histórico: quien quita un grupo
    está quitando una etiqueta. Para borrar una búsqueda está su propio botón,
    que sí avisa de lo que se lleva.
    """
    grupo = services.create_group(session, user.id, "Efímero")
    dentro = _search(session, user, "superviviente", group_id=grupo.id, position=0)
    session.commit()

    mudadas = services.delete_group(session, grupo)

    assert mudadas == 1
    assert session.get(Search, dentro.id) is not None
    assert dentro.group_id == services.grupo_por_defecto(session, user.id).id
    assert _nombres(session, user) == ["SIN GRUPO", "superviviente"]


def test_el_grupo_por_defecto_no_se_borra_con_cosas_dentro(session, user):
    """No hay a dónde mudarlas: sería la única forma de dejar huérfana una
    búsqueda, justo lo que la regla prohíbe."""
    _search(session, user, "dentro")
    session.commit()
    services.panel_blocks(session, user.id)  # las adopta
    defecto = services.grupo_por_defecto(session, user.id)

    assert services.delete_group(session, defecto) == 0
    assert session.get(services.SearchGroup, defecto.id) is not None


# --- el feed del grupo -----------------------------------------------------
def test_el_feed_mezcla_las_fotos_del_grupo_de_nueva_a_vieja(session, user):
    """La funcionalidad nueva: leer el bloque entero de corrido.

    Las fotos salen mezcladas de todas las búsquedas del grupo y en orden
    cronológico, que es como se mira el trabajo de un equipo; y las de otro
    grupo no se cuelan.
    """
    grupo = services.create_group(session, user.id, "Madrid")
    otro = services.create_group(session, user.id, "Fuera")
    a = _search(session, user, "a", group_id=grupo.id, position=0)
    b = _search(session, user, "b", group_id=grupo.id, position=1)
    ajena = _search(session, user, "ajena", group_id=otro.id, position=0)
    _add_photo(session, a, hours_ago=3)
    _add_photo(session, b, hours_ago=1)
    _add_photo(session, a, hours_ago=5)
    _add_photo(session, ajena, hours_ago=2)
    session.commit()

    assets, total = services.group_assets(session, grupo.id, page=1, per_page=10)

    assert total == 3
    assert [a.search_id for a in assets] == [b.id, a.id, a.id]  # 1 h, 3 h, 5 h
    fechas = [x.captured_at for x in assets]
    assert fechas == sorted(fechas, reverse=True)


def test_el_feed_de_un_grupo_vacio_no_revienta(session, user):
    grupo = services.create_group(session, user.id, "Vacío")
    session.commit()
    assert services.group_assets(session, grupo.id) == ([], 0)


def test_abrir_el_feed_no_apaga_ninguna_luz(session, user):
    """Es para leer, no para dar por vistas quince búsquedas de golpe: eso
    borraría justo lo que el panel usa para decir dónde ha entrado algo."""
    ayer = datetime.now(timezone.utc) - timedelta(days=1)
    grupo = services.create_group(session, user.id, "G")
    search = _search(session, user, "con novedades", group_id=grupo.id, seen_at=ayer)
    _add_photo(session, search, hours_ago=2)
    session.commit()

    services.group_assets(session, grupo.id)

    assert services._aware(search.seen_at) == ayer
    assert services.search_stats(session, search).has_new is True


def test_el_feed_destaca_lo_que_destacaria_cada_busqueda(session, user):
    """La frontera es la de CADA búsqueda, no una del grupo: una foto se destaca
    en el feed exactamente igual que dentro de su búsqueda."""
    grupo = services.create_group(session, user.id, "G")
    vista = _search(session, user, "ya vista", group_id=grupo.id, seen_at=utcnow())
    nueva = _search(
        session,
        user,
        "con novedades",
        group_id=grupo.id,
        seen_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    _add_photo(session, vista, hours_ago=2)
    reciente = _add_photo(session, nueva, hours_ago=2)
    session.commit()

    assets, _ = services.group_assets(session, grupo.id)
    destacadas = services.group_destacadas(session, assets, grupo.id)

    assert destacadas == {reciente.id}
