"""El reintento ante fallos pasajeros de una agencia.

Una sola página que tarda de más no debería marcar la ejecución como fallida
—y, con los avisos puestos, mandar un correo—, pero un fallo de verdad tiene
que seguir saliendo a la primera de cambio.
"""

from __future__ import annotations

import pytest

from app.ingest import runner


@pytest.fixture(autouse=True)
def _sin_esperas(monkeypatch):
    """El reintento espera 15 s en producción; aquí, cero."""
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)


def _que_falla(veces, excepcion):
    """Un trabajo que falla las primeras ``veces`` llamadas y luego funciona."""
    estado = {"llamadas": 0}

    def trabajo():
        estado["llamadas"] += 1
        if estado["llamadas"] <= veces:
            raise excepcion
        return "hecho"

    return trabajo, estado


def test_un_tropiezo_se_reintenta_y_sale_bien():
    trabajo, estado = _que_falla(1, TimeoutError("Timeout 45000ms exceeded"))
    assert runner.con_reintento(trabajo, "reuters/Juan Medina") == "hecho"
    assert estado["llamadas"] == 2


def test_si_falla_dos_veces_el_error_sube():
    """El aviso por correo tiene que seguir saliendo cuando la agencia está rota."""
    trabajo, estado = _que_falla(99, TimeoutError("Timeout 45000ms exceeded"))
    with pytest.raises(TimeoutError):
        runner.con_reintento(trabajo, "reuters/Juan Medina")
    assert estado["llamadas"] == runner.INTENTOS == 2


def test_lo_que_va_bien_a_la_primera_no_se_repite():
    trabajo, estado = _que_falla(0, TimeoutError("nunca"))
    assert runner.con_reintento(trabajo) == "hecho"
    assert estado["llamadas"] == 1


@pytest.mark.parametrize(
    "mensaje",
    [
        "Timeout 45000ms exceeded",
        "Reuters: no result cards at https://www.reutersconnect.com/all?search=x",
        "Connection reset by peer",
    ],
)
def test_los_fallos_pasajeros_merecen_reintento(mensaje):
    assert runner._merece_reintento(RuntimeError(mensaje)) is True


@pytest.mark.parametrize(
    "mensaje",
    [
        "el login de Reuters no se completó (bot-wall o credenciales)",
        "live mode needs credentials",
    ],
)
def test_lo_que_un_reintento_no_puede_arreglar_no_se_reintenta(mensaje):
    """Sin credenciales seguirá sin haberlas; y ante un muro anti-bot que pide
    un humano, insistir solo empeora la reputación del navegador."""
    trabajo, estado = _que_falla(99, RuntimeError(mensaje))
    with pytest.raises(RuntimeError):
        runner.con_reintento(trabajo, "reuters/x")
    assert estado["llamadas"] == 1  # ni un segundo intento


def test_hay_exactamente_un_reintento():
    """Dos intentos en total: ni uno (no filtraría nada) ni tres (castiga a la agencia)."""
    assert runner.INTENTOS == 2


def test_sin_sesion_no_se_reintenta_y_no_depende_del_texto():
    """La falta de sesión de Reuters no se arregla esperando 15 segundos.

    Y la decisión ya no puede depender del texto: cambiar una palabra del
    mensaje reactivó el reintento sin que ningún test lo viera, y el segundo
    intento moría en el hilo envenenado por el open() fallido con un «Sync API
    inside the asyncio loop» que tapaba el mensaje útil. Ahora lo dice la
    propia excepción, se escriba como se escriba su texto.
    """
    from app.ingest.live_base import SinSesionError

    trabajo, estado = _que_falla(99, SinSesionError("da igual cómo se redacte esto"))
    with pytest.raises(SinSesionError):
        runner.con_reintento(trabajo, "reuters/x")
    assert estado["llamadas"] == 1  # ni un segundo intento

    # Y la red por texto sigue cubriendo a quien no lleve la marca.
    assert runner._merece_reintento(RuntimeError("no hay sesión de Reuters viva")) is False
