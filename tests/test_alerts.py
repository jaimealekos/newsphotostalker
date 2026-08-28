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


def _entrega_capturada(monkeypatch):
    """Intercepta el POST real del webhook y devuelve lo que salió por él."""
    import io
    import json

    salidas: list[dict] = []

    class _Resp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, timeout=None):
        salidas.append(json.loads(req.data.decode()))
        return _Resp()

    monkeypatch.setattr("app.alerts.urllib.request.urlopen", _urlopen)
    return salidas


def test_la_postdata_viaja_al_final_de_todo_aviso(monkeypatch):
    """La coletilla configurada (qué hacer al recibir el aviso) se añade en el
    único punto de salida, para que ningún aviso pueda olvidarse de ella."""
    salidas = _entrega_capturada(monkeypatch)
    cfg = AlertsConfig(
        enabled=True,
        webhook_url="http://alerts.test/hook",
        postdata="→ Abre Claude Code y avísale.",
    )
    assert alerts._post(cfg, "asunto x", "cuerpo y") is True
    assert salidas[0]["subject"] == "asunto x"
    assert salidas[0]["message"] == "cuerpo y\n\n→ Abre Claude Code y avísale."


def test_sin_postdata_el_aviso_va_tal_cual(monkeypatch):
    salidas = _entrega_capturada(monkeypatch)
    cfg = AlertsConfig(enabled=True, webhook_url="http://alerts.test/hook")
    assert alerts._post(cfg, "asunto", "cuerpo") is True
    assert salidas[0]["message"] == "cuerpo"


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


def test_un_lote_con_dos_busquedas_avisa_una_vez_por_averia(settings, monkeypatch):
    """La otra vía de la tormenta del 27-08-2026, con DOS búsquedas.

    El flanco es por AGENCIA y ``record_run`` se llamaba por BÚSQUEDA: con una
    búsqueda rota de forma persistente y otra sana, cada ciclo de refresco
    avisaba del fallo y acto seguido REARMABA el disparador con el éxito de la
    hermana — un correo idéntico por ciclo (17:03, 19:03, 21:03) de la misma
    avería continua. El lote da la agencia por buena solo si TODAS sus búsquedas
    fueron bien. Con el código viejo, esto manda tres avisos.
    """
    from app.ingest.runner import RunResult, _avisa_del_lote

    monkeypatch.setattr("app.ingest.runner.get_settings", lambda: settings)
    enviados: list[str] = []
    monkeypatch.setattr(alerts, "_post", lambda cfg, asunto, mensaje: enviados.append(asunto) or True)

    def ciclo_con_una_rota():
        _avisa_del_lote([
            ("reuters", RunResult(1, "error", message="LiveAdapterError: no result cards")),
            ("reuters", RunResult(2, "ok")),
        ])

    for _ in range(3):
        ciclo_con_una_rota()
    assert len(enviados) == 1

    # Y cuando se arregla, el disparador se rearma: la próxima avería sí avisa.
    _avisa_del_lote([("reuters", RunResult(1, "ok")), ("reuters", RunResult(2, "ok"))])
    ciclo_con_una_rota()
    assert len(enviados) == 2


def test_status_refleja_el_estado(settings, sent):
    assert alerts.status(settings, "reuters") is None
    alerts.record_run(settings, "reuters", ok=True)
    assert alerts.status(settings, "reuters") == "ok"
    alerts.record_run(settings, "reuters", ok=False, error="x")
    assert alerts.status(settings, "reuters") == "failing"
