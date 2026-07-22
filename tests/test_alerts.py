"""Tests de la máquina de estados de avisos por flanco (app/alerts.py)."""

from __future__ import annotations

import pytest

from app import alerts
from app.config import AlertsConfig, Settings


@pytest.fixture()
def settings(tmp_path):
    s = Settings(data_dir=tmp_path)
    s.alerts = AlertsConfig(enabled=True, webhook_url="http://alerts.test/hook")
    return s


@pytest.fixture()
def sent(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(alerts, "_send", lambda cfg, agency, error: calls.append((agency, error)) or True)
    return calls


def test_primer_fallo_avisa_y_los_siguientes_callan(settings, sent):
    alerts.record_run(settings, "reuters", ok=False, error="bot-wall")
    alerts.record_run(settings, "reuters", ok=False, error="bot-wall")
    alerts.record_run(settings, "reuters", ok=False, error="bot-wall")
    assert sent == [("reuters", "bot-wall")]


def test_recuperarse_rearma_el_disparador(settings, sent):
    alerts.record_run(settings, "reuters", ok=False, error="a")
    alerts.record_run(settings, "reuters", ok=True)
    alerts.record_run(settings, "reuters", ok=False, error="b")
    assert sent == [("reuters", "a"), ("reuters", "b")]


def test_exito_no_avisa(settings, sent):
    alerts.record_run(settings, "reuters", ok=True)
    assert sent == []


def test_estado_persiste_entre_procesos(settings, sent):
    alerts.record_run(settings, "reuters", ok=False, error="x")
    # Nuevo "proceso": el estado se relee del fichero, no de memoria.
    alerts.record_run(settings, "reuters", ok=False, error="x")
    assert len(sent) == 1
    assert (settings.data_dir / "alert_state.json").exists()


def test_envio_fallido_reintenta_al_siguiente_fallo(settings, monkeypatch):
    intentos = []
    monkeypatch.setattr(alerts, "_send", lambda cfg, agency, error: intentos.append(error) or False)
    alerts.record_run(settings, "reuters", ok=False, error="x")
    alerts.record_run(settings, "reuters", ok=False, error="x")
    assert len(intentos) == 2  # no se marcó "failing" hasta entregar el aviso


def test_agencia_no_vigilada_se_ignora(settings, sent):
    alerts.record_run(settings, "ap", ok=False, error="x")
    assert sent == []


def test_desactivado_no_avisa(settings, sent):
    settings.alerts.enabled = False
    alerts.record_run(settings, "reuters", ok=False, error="x")
    assert sent == []


def test_status_refleja_el_estado(settings, sent):
    assert alerts.status(settings, "reuters") is None
    alerts.record_run(settings, "reuters", ok=True)
    assert alerts.status(settings, "reuters") == "ok"
    alerts.record_run(settings, "reuters", ok=False, error="x")
    assert alerts.status(settings, "reuters") == "failing"
