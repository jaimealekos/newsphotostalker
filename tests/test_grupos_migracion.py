"""Los separadores de la 1.2 se convierten en grupos sin mover nada de sitio.

Esta migración toca el panel de alguien que ya lo tiene colocado a mano, así que
lo que se prueba aquí no es que «funcione»: es que **al actualizar, la pantalla
salga exactamente igual que estaba**. El separador era una raya suelta y el
bloque que abría era todo lo que venía detrás hasta el siguiente; esa lectura —la
que el usuario ya veía— es la que tiene que quedar convertida en grupos.

El caso de la primera prueba es el panel real del 02-09-2026: 22 búsquedas y un
único separador SIN rótulo entre «Alvaro Barrientos» y «APTOPIX».
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.database import Base, _migrate_grupos
from app.models import Search, SearchGroup, User


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    # La tabla vieja ya no está en los modelos: se recrea a mano, que es como se
    # la va a encontrar la migración en una base de la 1.2.
    s.execute(
        text(
            "CREATE TABLE separators (id INTEGER PRIMARY KEY, user_id INTEGER, "
            "label VARCHAR(200), position INTEGER)"
        )
    )
    yield s
    s.close()


@pytest.fixture()
def user(session):
    u = User(username="yo", password_hash="x")
    session.add(u)
    session.flush()
    return u


def _busqueda(session, user, name, position):
    search = Search(
        name=name, agency="ap", kind="text", query=name, user_id=user.id, position=position
    )
    session.add(search)
    return search


def _separador(session, user, label, position):
    session.execute(
        text("INSERT INTO separators (user_id, label, position) VALUES (:u, :l, :p)"),
        {"u": user.id, "l": label, "p": position},
    )


def _panel(session, user):
    """El panel aplanado: ['GRUPO', 'búsqueda', ...] en el orden que se ve."""
    salida = []
    grupos = session.scalars(
        select(SearchGroup).where(SearchGroup.user_id == user.id).order_by(SearchGroup.position)
    )
    for grupo in grupos:
        salida.append(grupo.name.upper())
        salida += [
            s.name
            for s in session.scalars(
                select(Search).where(Search.group_id == grupo.id).order_by(Search.position)
            )
        ]
    return salida


def test_el_panel_real_sale_igual_que_estaba(session, user):
    """El caso de verdad: un separador sin rótulo parte el panel en dos bloques.

    Lo que iba ANTES del separador no tenía ninguna raya encima, así que no
    pertenecía a ningún bloque: cae en «Sin grupo». Lo que iba detrás es el
    bloque que el separador abría.
    """
    for i, nombre in enumerate(
        ["Emilio Morenatti", "Bernat Armangué", "Francisco Seco", "Vadim Ghirda",
         "Andrés Kudacki", "Alvaro Barrientos"]
    ):
        _busqueda(session, user, nombre, i)
    _separador(session, user, "", 6)
    for i, nombre in enumerate(["APTOPIX", "APTOPIX Spain", "Rodrigo Abd"], start=7):
        _busqueda(session, user, nombre, i)
    session.commit()

    _migrate_grupos(session, user.id)
    session.commit()

    assert _panel(session, user) == [
        "SIN GRUPO",
        "Emilio Morenatti", "Bernat Armangué", "Francisco Seco", "Vadim Ghirda",
        "Andrés Kudacki", "Alvaro Barrientos",
        "SIN TÍTULO",
        "APTOPIX", "APTOPIX Spain", "Rodrigo Abd",
    ]


def test_un_separador_con_rotulo_da_nombre_a_su_grupo(session, user):
    _busqueda(session, user, "suelta", 0)
    _separador(session, user, "Deportes", 1)
    _busqueda(session, user, "dentro", 2)
    session.commit()

    _migrate_grupos(session, user.id)
    session.commit()

    assert _panel(session, user) == ["SIN GRUPO", "suelta", "DEPORTES", "dentro"]


def test_sin_separadores_todo_cae_en_un_solo_grupo(session, user):
    """Quien nunca usó separadores no debe encontrarse el panel troceado."""
    for i, nombre in enumerate(["a", "b", "c"]):
        _busqueda(session, user, nombre, i)
    session.commit()

    _migrate_grupos(session, user.id)
    session.commit()

    assert _panel(session, user) == ["SIN GRUPO", "a", "b", "c"]


def test_la_tabla_vieja_se_retira_cuando_ya_no_queda_nada(session, user):
    _separador(session, user, "Algo", 0)
    _busqueda(session, user, "x", 1)
    session.commit()

    _migrate_grupos(session, user.id)
    session.commit()

    quedan = list(
        session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='separators'")
        )
    )
    assert quedan == []


def test_pasar_dos_veces_no_duplica_grupos(session, user):
    """El arranque corre en cada encendido: la segunda vez no puede rehacer nada.

    Sin esta garantía, cada reinicio del contenedor añadiría otro «Sin grupo» y
    el panel se llenaría de bloques vacíos.
    """
    _busqueda(session, user, "x", 0)
    _separador(session, user, "Uno", 1)
    _busqueda(session, user, "y", 2)
    session.commit()

    _migrate_grupos(session, user.id)
    session.commit()
    antes = _panel(session, user)

    _migrate_grupos(session, user.id)  # la tabla vieja ya no existe
    session.commit()

    assert _panel(session, user) == antes


def test_una_busqueda_sin_grupo_siempre_acaba_en_uno(session, user):
    """La red de seguridad: da igual por dónde llegue una búsqueda huérfana."""
    session.execute(text("DROP TABLE separators"))
    grupo = SearchGroup(user_id=user.id, name="Ya existente", position=0)
    session.add(grupo)
    session.flush()
    _busqueda(session, user, "huérfana", 0)
    session.commit()

    _migrate_grupos(session, user.id)
    session.commit()

    assert _panel(session, user) == ["YA EXISTENTE", "SIN GRUPO", "huérfana"]
