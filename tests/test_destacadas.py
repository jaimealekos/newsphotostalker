"""Las fotos que aún no has visto salen destacadas en la rejilla.

La regla tiene tres conductas, y la gracia está en que se distinguen:

  1. abres una foto            -> esa deja de destacarse, para siempre
  2. recargas o pasas de página -> las demás SIGUEN destacadas
  3. cierras el navegador y vuelves -> ya no queda ninguna destacada

Aquí se prueba la regla (qué se destaca y cuándo), y en
``test_destacadas_web.py`` el mecanismo que la sostiene: la cookie de sesión.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import services
from app.database import Base
from app.models import Asset, Search, User

AHORA = datetime.now(timezone.utc)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture()
def busqueda(session):
    u = User(username="yo", password_hash="x")
    session.add(u)
    session.flush()
    s = Search(name="b", agency="ap", kind="text", query="b", user_id=u.id)
    session.add(s)
    session.flush()
    return s


def _foto(session, busqueda, *, hace_horas, vista=False):
    a = Asset(
        search_id=busqueda.id,
        agency="ap",
        external_id=f"id-{hace_horas}-{vista}",
        downloaded_at=AHORA - timedelta(hours=hace_horas),
        seen_at=AHORA if vista else None,
    )
    session.add(a)
    session.flush()
    return a


def test_se_destaca_lo_llegado_despues_de_tu_ultima_visita(session, busqueda):
    frontera = AHORA - timedelta(hours=5)
    vieja = _foto(session, busqueda, hace_horas=9)
    nueva = _foto(session, busqueda, hace_horas=2)

    destacadas = services.ids_destacables([vieja, nueva], frontera)
    assert destacadas == {nueva.id}


def test_una_foto_ya_abierta_no_se_destaca_aunque_sea_nueva(session, busqueda):
    """Conducta 1: verla la desmarca, y es definitivo."""
    frontera = AHORA - timedelta(hours=5)
    nueva_sin_ver = _foto(session, busqueda, hace_horas=2)
    nueva_ya_vista = _foto(session, busqueda, hace_horas=1, vista=True)

    destacadas = services.ids_destacables([nueva_sin_ver, nueva_ya_vista], frontera)
    assert destacadas == {nueva_sin_ver.id}


def test_sin_frontera_no_se_destaca_nada(session, busqueda):
    """Primera visita: encender la rejilla entera no informaría de nada."""
    fotos = [_foto(session, busqueda, hace_horas=h) for h in (1, 20, 300)]
    assert services.ids_destacables(fotos, None) == set()


def test_la_frontera_no_se_mueve_al_recargar(session, busqueda):
    """Conducta 2: mientras la frontera sea la misma, se destaca lo mismo.

    Es el efecto de congelarla en la cookie de sesión: `mark_seen` mueve el
    `seen_at` de la búsqueda en cada visita, pero la rejilla no mira eso.
    """
    frontera = AHORA - timedelta(hours=5)
    fotos = [_foto(session, busqueda, hace_horas=2), _foto(session, busqueda, hace_horas=9)]

    primera = services.ids_destacables(fotos, frontera)
    services.mark_seen(session, busqueda)      # visitar mueve el visto persistente
    segunda = services.ids_destacables(fotos, frontera)
    assert primera == segunda != set()


def test_tras_cerrar_el_navegador_la_frontera_es_el_visto_ya_avanzado(session, busqueda):
    """Conducta 3: sin cookie, la frontera pasa a ser el `seen_at` que avanzó
    durante la visita anterior, y entonces no queda nada destacado."""
    fotos = [_foto(session, busqueda, hace_horas=2), _foto(session, busqueda, hace_horas=9)]
    services.mark_seen(session, busqueda)      # la visita de antes de cerrar

    assert services.ids_destacables(fotos, busqueda.seen_at) == set()


def test_marcar_vista_es_idempotente_y_no_reescribe_la_fecha(session, busqueda):
    foto = _foto(session, busqueda, hace_horas=1)
    services.mark_asset_seen(session, foto)
    primera = foto.seen_at
    assert primera is not None

    services.mark_asset_seen(session, foto)    # volver a abrirla
    assert foto.seen_at == primera             # se conserva cuándo la viste tú
