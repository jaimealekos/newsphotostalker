"""El lote de run_all_enabled y el veredicto por agencia, con el cableado REAL.

La revisión de 08-2026 encontró que el arreglo de la tormenta solo estaba
fijado a medias: el test del veredicto llamaba a _avisa_del_lote directamente,
así que revertir el ``avisa=False`` de run_all_enabled —la causa exacta de los
correos de las 17:03/19:03/21:03 del 27-08-2026— dejaba la suite en verde.
Estos tests ejecutan run_all_enabled de verdad (con la BD y run_search
doblados) y fijan las piezas que faltaban: un record_run por agencia, el
reventón que ni para el lote ni desaparece del veredicto, el borrado a mitad
de lote que no es una avería, y el camino manual que solo anota fallos.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from app.ingest import runner
from app.ingest.runner import RunResult


def _bd_con(filas, monkeypatch):
    """session_scope de pega: esas (id, agency) son las búsquedas activas."""

    @contextmanager
    def _scope():
        yield SimpleNamespace(execute=lambda _q: SimpleNamespace(all=lambda: filas))

    monkeypatch.setattr(runner, "session_scope", _scope)


def _capturas(monkeypatch):
    """Captura record_run y neutraliza get_settings."""
    avisos: list[tuple] = []
    monkeypatch.setattr(runner, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        runner.alerts, "record_run",
        lambda s, agency, ok, error=None: avisos.append((agency, ok, error)),
    )
    return avisos


# --- el lote ---------------------------------------------------------------


def test_el_lote_pide_no_avisar_por_busqueda_y_avisa_una_vez_por_agencia(monkeypatch):
    """run_all_enabled DE VERDAD: cada búsqueda corre con avisa=False y el
    veredicto sale una vez por agencia — buena solo si TODAS fueron bien."""
    _bd_con([(1, "reuters"), (2, "reuters"), (3, "ap")], monkeypatch)
    avisos = _capturas(monkeypatch)
    llamadas: list[dict] = []

    def _run_search_doble(sid, limit=100, **kw):
        llamadas.append(kw)
        if sid == 1:
            return RunResult(sid, "error", message="no result cards")
        return RunResult(sid, "ok")

    monkeypatch.setattr(runner, "run_search", _run_search_doble)

    resultados = runner.run_all_enabled()

    assert len(resultados) == 3
    assert all(kw.get("avisa") is False for kw in llamadas)  # nadie avisa por su cuenta
    assert sorted(avisos) == [("ap", True, None), ("reuters", False, "no result cards")]


def test_un_reventon_no_para_el_lote_y_cuenta_como_fallo(monkeypatch):
    """Una búsqueda que revienta FUERA del runner (la BD bloqueada al anotar el
    RunLog) no puede ni abortar el lote ni dejarle a la agencia un veredicto
    limpio: sin esto, esa avería quedaba en silencio eterno y el éxito de las
    hermanas rearmaba el flanco — las dos mitades de lo que se vino a arreglar."""
    _bd_con([(1, "reuters"), (2, "reuters")], monkeypatch)
    avisos = _capturas(monkeypatch)

    def _run_search_doble(sid, limit=100, **kw):
        if sid == 1:
            raise RuntimeError("database is locked")
        return RunResult(sid, "ok")

    monkeypatch.setattr(runner, "run_search", _run_search_doble)

    resultados = runner.run_all_enabled()

    assert [r.search_id for r in resultados] == [1, 2]  # la segunda corrió igual
    assert resultados[0].status == "error"
    assert avisos == [("reuters", False, "RuntimeError: database is locked")]


def test_borrar_una_busqueda_a_mitad_de_lote_no_es_averia(monkeypatch):
    """La instantánea de ids se toma al empezar y el lote tarda minutos: borrar
    una búsqueda desde el panel produce «search not found», que es
    administración. No puede convertirse en «la agencia ha dejado de funcionar»."""
    _bd_con([(1, "reuters"), (2, "reuters")], monkeypatch)
    avisos = _capturas(monkeypatch)

    def _run_search_doble(sid, limit=100, **kw):
        if sid == 1:
            return RunResult(sid, "error", message="search not found")
        return RunResult(sid, "ok")

    monkeypatch.setattr(runner, "run_search", _run_search_doble)

    runner.run_all_enabled()
    assert avisos == [("reuters", True, None)]


def test_si_solo_queda_el_borrado_no_hay_veredicto(monkeypatch):
    """Sin ninguna búsqueda de verdad, la agencia ni se juzga: un ok aquí
    rearmaría el flanco sin prueba ninguna, y un fallo avisaría de un borrado."""
    _bd_con([(1, "reuters")], monkeypatch)
    avisos = _capturas(monkeypatch)
    monkeypatch.setattr(
        runner, "run_search",
        lambda sid, limit=100, **kw: RunResult(sid, "error", message="search not found"),
    )

    runner.run_all_enabled()
    assert avisos == []


# --- el camino manual (botón ↻, backfill, alta de búsqueda) ----------------


def test_una_ejecucion_manual_buena_no_rearma_el_disparador(monkeypatch):
    """El camino manual ve UNA búsqueda y el disparador es de la agencia entera:
    su éxito no puede dar la agencia por sana mientras una hermana sigue rota
    (la variante manual de la tormenta). Rearmar es cosa del lote."""
    avisos = _capturas(monkeypatch)

    runner._avisa_de_un_run(SimpleNamespace(), "reuters", RunResult(1, "ok"))

    assert avisos == []


def test_una_ejecucion_manual_fallida_si_avisa(monkeypatch):
    """El primer fallo de un tramo tiene que salir también por el camino manual;
    los repetidos ya los silencia el flanco de alerts."""
    avisos = _capturas(monkeypatch)

    runner._avisa_de_un_run(SimpleNamespace(), "reuters", RunResult(1, "error", message="boom"))

    assert avisos == [("reuters", False, "boom")]
