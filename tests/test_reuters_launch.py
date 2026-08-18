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


# --- un open() fallido no puede dejar Playwright vivo ------------------------
#
# La fuga envenenaba el hilo: el sync de Playwright deja su bucle de asyncio
# CORRIENDO (greenlets), y el reintento de la ingesta —mismo hilo— moría con
# «Sync API inside the asyncio loop», tapando el error real. Y quien llamó a
# open() nunca recibió el adaptador, así que nadie más podía cerrarlo:
# BaseAdapter.__exit__ solo corre si __enter__ (=open) terminó.


def _settings_falsos(tmp_path):
    return types.SimpleNamespace(
        data_dir=tmp_path,
        playwright=types.SimpleNamespace(
            user_data_dir=str(tmp_path / "browser"),
            headless=True,
            timeout_ms=1000,
            executable_path="chrome.exe",  # que no busque navegadores de verdad
        ),
    )


def _con_playwright_falso(monkeypatch, chromium):
    """Sustituye sync_playwright por uno de pega y devuelve su registro."""
    import playwright.sync_api as psync

    registro = {"stop": 0}
    pw = types.SimpleNamespace(chromium=chromium, stop=lambda: registro.__setitem__("stop", registro["stop"] + 1))
    monkeypatch.setattr(psync, "sync_playwright", lambda: types.SimpleNamespace(start=lambda: pw))
    return registro


def test_si_el_navegador_no_arranca_playwright_se_para(monkeypatch, tmp_path):
    registro = _con_playwright_falso(monkeypatch, _Chromium(fallos=99, error="spawn UNKNOWN"))
    ad = _Adaptador(_settings_falsos(tmp_path), None)
    with pytest.raises(LiveAdapterError):
        ad.open()
    assert registro["stop"] == 1  # el driver no queda vivo
    assert ad._pw is None and ad._context is None  # y el adaptador, limpio


def test_si_el_login_lanza_se_cierra_todo(monkeypatch, tmp_path):
    cerrado = {"context": 0}

    class _Contexto:
        def set_default_timeout(self, *_):
            pass

        def new_page(self):
            return "PAGINA"

        def close(self):
            cerrado["context"] += 1

    class _ChromiumOK:
        def launch_persistent_context(self, **_):
            return _Contexto()

    registro = _con_playwright_falso(monkeypatch, _ChromiumOK())

    class _SinSesion(_Adaptador):
        requires_login = True

        def login(self):
            raise LiveAdapterError("no hay sesión de Reuters viva…")

    ad = _SinSesion(_settings_falsos(tmp_path), None)
    with pytest.raises(LiveAdapterError):
        ad.open()
    assert cerrado["context"] == 1
    assert registro["stop"] == 1
    assert ad._pw is None and ad._context is None
