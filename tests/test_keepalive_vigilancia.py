"""El vigilante del keep-alive: que su ausencia deje de ser invisible.

El keep-alive es la única pieza que no escribía en ninguna parte. Sus fallos se
veían (van a error y disparan el aviso), pero su AUSENCIA no: si dejaba de
programarse, el silencio era idéntico al de uno impecable, y el primer síntoma
llegaba días después con la sesión de Reuters caducada.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.ingest import keepalive


@pytest.fixture()
def settings(tmp_path):
    """Config mínima: keep-alive cada 180 min y avisos por webhook activos."""
    return SimpleNamespace(
        data_dir=tmp_path,
        reuters_keepalive_minutes=180,
        alerts=SimpleNamespace(
            enabled=True,
            webhook_url="http://webhook.invalido/x",
            agencies=["reuters"],
            timeout_s=1,
        ),
    )


@pytest.fixture()
def enviados(monkeypatch):
    """Intercepta la entrega para no salir a la red y poder mirar qué se manda."""
    salida = []

    def _post(cfg, subject, message):
        salida.append((subject, message))
        return True

    monkeypatch.setattr("app.alerts._post", _post)
    return salida


def _envejece(settings, horas):
    """Coloca la última señal N horas atrás."""
    cuando = datetime.now(timezone.utc) - timedelta(hours=horas)
    keepalive._ruta_senal(settings).write_text(
        '{"ultima_senal": "%s", "motivo": "test"}' % cuando.isoformat(timespec="seconds")
    )


def test_la_primera_vez_no_avisa_y_deja_referencia(settings, enviados):
    """Sin fichero previo no se puede saber nada: se anota el punto de partida."""
    assert keepalive.revisa_atraso(settings) is False
    assert enviados == []
    assert keepalive.ultima_senal(settings) is not None


def test_señal_reciente_no_avisa(settings, enviados):
    _envejece(settings, horas=1)
    assert keepalive.revisa_atraso(settings) is False
    assert enviados == []


def test_justo_dentro_del_margen_no_avisa(settings, enviados):
    """180 min x 2 intervalos = 6 h de gracia; a las 5 h aún no."""
    _envejece(settings, horas=5)
    assert keepalive.revisa_atraso(settings) is False
    assert enviados == []


def test_pasado_el_margen_avisa_una_sola_vez(settings, enviados):
    _envejece(settings, horas=7)
    assert keepalive.revisa_atraso(settings) is True
    assert len(enviados) == 1
    asunto, mensaje = enviados[0]
    assert "keep-alive" in asunto
    # El mensaje tiene que explicar por qué importa, no solo que pasó.
    assert "caducará" in mensaje

    # Segunda ronda con el mismo problema: silencio, ya se avisó.
    assert keepalive.revisa_atraso(settings) is True
    assert len(enviados) == 1


def test_al_recuperarse_se_rearma_el_disparador(settings, enviados):
    _envejece(settings, horas=7)
    keepalive.revisa_atraso(settings)
    assert len(enviados) == 1

    keepalive.marca_senal(settings)          # el keep-alive vuelve a la vida
    assert keepalive.revisa_atraso(settings) is False

    _envejece(settings, horas=7)             # y vuelve a caerse más tarde
    assert keepalive.revisa_atraso(settings) is True
    assert len(enviados) == 2                # avisa de nuevo: es otro tramo


def test_con_el_keepalive_desactivado_no_vigila_nada(settings, enviados):
    """Si está apagado a propósito, su silencio es lo correcto."""
    settings.reuters_keepalive_minutes = 0
    _envejece(settings, horas=99)
    assert keepalive.revisa_atraso(settings) is False
    assert enviados == []


def test_la_senal_sobrevive_a_la_lectura(settings):
    keepalive.marca_senal(settings, motivo="arranque")
    guardada = keepalive.ultima_senal(settings)
    assert guardada is not None
    assert (datetime.now(timezone.utc) - guardada).total_seconds() < 60


def test_un_fichero_corrupto_no_revienta_la_vigilancia(settings, enviados):
    keepalive._ruta_senal(settings).write_text("{no es json")
    # Se comporta como si no hubiera referencia: anota y sigue, sin avisar.
    assert keepalive.revisa_atraso(settings) is False
    assert keepalive.ultima_senal(settings) is not None
