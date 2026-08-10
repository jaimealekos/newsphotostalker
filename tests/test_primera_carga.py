"""Una búsqueda recién creada se llena sola, sin pedírselo.

Antes había que pulsar «↻ Ejecutar» a mano, o esperar al refresco global, que
puede tardar horas. Nadie crea una búsqueda para no mirarla.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import services
from app.database import Base
from app.models import User


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


@pytest.fixture()
def lanzadas(monkeypatch):
    """Intercepta el disparo para no arrancar un planificador de verdad."""
    ids = []
    monkeypatch.setattr(services.scheduler, "run_now", ids.append)
    return ids


def _form(**kw):
    datos = dict(name="", agency="ap", kind="text", query="Madrid", cadence_minutes=360)
    datos.update(kw)
    return datos


def test_crear_desde_el_panel_lanza_la_primera_carga(session, user, lanzadas):
    search = services.create_search(session, _form(), user.id, primera_carga=True)
    assert lanzadas == [search.id]


def test_sin_pedirlo_no_se_lanza_nada(session, user, lanzadas):
    """El valor por defecto no dispara: crear una búsqueda desde código o desde
    los tests no debe poner a trabajar al planificador."""
    services.create_search(session, _form(), user.id)
    assert lanzadas == []


def test_una_busqueda_desactivada_no_se_carga(session, user, lanzadas):
    """Si nace apagada, encenderla es cosa suya: no se busca a su espalda."""
    services.create_search(session, _form(enabled=False), user.id, primera_carga=True)
    assert lanzadas == []


def test_la_primera_tanda_no_sale_destacada(session, user, lanzadas):
    """Nace «vista»: sus primeras fotos no son novedades desde tu última visita,
    porque no había visita anterior. La rejilla se estrena en calma."""
    search = services.create_search(session, _form(), user.id, primera_carga=True)
    assert search.seen_at is not None
