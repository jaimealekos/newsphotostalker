"""El arranque del navegador reintenta cuando el perfil sigue tomado.

Chromium solo admite un proceso por ``user-data-dir``. Aunque los runs van
serializados, el Chromium anterior tarda en morir tras ``close()``, y si el
siguiente arranca antes de que suelte el perfil, un Chrome real reenvía al que
agoniza y se cierra («Target page, context or browser has been closed»). Pasó al
iniciar sesión en Reuters justo después de una búsqueda. Se reintenta con un
margen; un fallo que NO es de perfil (no hay navegador) se relanza al momento.
"""

from __future__ import annotations

import types

import pytest

from app.ingest import live_base
from app.ingest.live_base import LiveAdapter, LiveAdapterError, _perfil_ocupado

RACE = "Target page, context or browser has been closed"


class _Chromium:
    def __init__(self, fallos: int, error: str = RACE):
        self.fallos = fallos
        self.error = error
        self.llamadas = 0

    def launch_persistent_context(self, **_):
        self.llamadas += 1
        if self.llamadas <= self.fallos:
            raise RuntimeError(self.error)
        return "CONTEXTO"


class _Adaptador(LiveAdapter):
    """Concreta mínima: solo se prueba ``_lanzar``, no search/download."""

    agency = "reuters"

    def search(self, **_):  # pragma: no cover - no se usa aquí
        return []

    def download(self, *_):  # pragma: no cover - no se usa aquí
        return None


def _adapter(fallos: int, error: str = RACE) -> LiveAdapter:
    ad = _Adaptador.__new__(_Adaptador)  # sin abrir nada de verdad
    ad._pw = types.SimpleNamespace(chromium=_Chromium(fallos, error))
    return ad


@pytest.fixture(autouse=True)
def _sin_esperas(monkeypatch):
    # Que los reintentos no tarden 2s cada uno en la prueba.
    monkeypatch.setattr(live_base.time, "sleep", lambda *_: None)


def test_reintenta_y_acaba_arrancando(tmp_path):
    ad = _adapter(fallos=2)
    assert ad._lanzar({}, tmp_path, "chrome.exe") == "CONTEXTO"
    assert ad._pw.chromium.llamadas == 3  # dos fallos de perfil y a la tercera


def test_se_rinde_tras_agotar_los_reintentos(tmp_path):
    ad = _adapter(fallos=99)
    with pytest.raises(LiveAdapterError):
        ad._lanzar({}, tmp_path, "chrome.exe")
    assert ad._pw.chromium.llamadas == 4  # el tope de intentos, no infinito


def test_un_fallo_que_no_es_de_perfil_no_se_reintenta(tmp_path):
    ad = _adapter(fallos=99, error="spawn UNKNOWN")
    with pytest.raises(LiveAdapterError):
        ad._lanzar({}, tmp_path, None)
    assert ad._pw.chromium.llamadas == 1  # ni un reintento: no hay navegador


def test_clasificador_de_errores():
    assert _perfil_ocupado(RuntimeError(RACE))
    assert _perfil_ocupado(RuntimeError("Failed to create a ProcessSingleton"))
    assert not _perfil_ocupado(RuntimeError("spawn UNKNOWN"))
    assert not _perfil_ocupado(RuntimeError("Executable doesn't exist at ..."))
