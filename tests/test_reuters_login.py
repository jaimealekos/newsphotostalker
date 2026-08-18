"""Login de Reuters en dos pasos: abrir el navegador y comprobar la sesión.

El login usa el navegador normal del sistema (para pasar DataDome) y NO puede
saber a ciencia cierta cuándo la persona ha terminado: cerrar la ventana no cierra
Chrome si sigue en segundo plano, así que esperar a que el proceso muera se colgaba
—el panel se quedaba en «esperando a que entres…»—. Ahora es la persona quien
confirma con un botón, y la app cierra el navegador del login antes de comprobar.
"""

from __future__ import annotations

import subprocess
import sys

from app.ingest import reuters_login as rl


def test_estados_del_boton():
    """Cada estado enseña el control correcto en el panel."""
    rl.STATUS.set("idle", "")
    assert not rl.STATUS.awaiting and not rl.STATUS.busy  # -> "iniciar sesión"

    rl.STATUS.set("open", "")
    assert rl.STATUS.awaiting  # -> "he entrado, comprobar"

    rl.STATUS.set("waiting_lock", "")
    assert rl.STATUS.busy and not rl.STATUS.awaiting  # -> desactivado

    rl.STATUS.set("checking", "")
    assert rl.STATUS.busy and not rl.STATUS.awaiting

    rl.STATUS.set("ok", "sesión guardada")
    assert not rl.STATUS.awaiting and not rl.STATUS.busy  # -> "iniciar sesión" otra vez


def test_cierra_un_navegador_que_no_muere_solo():
    """El caso que colgaba: un proceso vivo (Chrome en segundo plano) se cierra."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with rl._PROC_LOCK:
        rl._PROC = proc
    rl._cierra_navegador()
    assert proc.poll() is not None  # terminado
    assert rl._PROC is None  # y olvidado


def test_cerrar_sin_navegador_no_falla():
    with rl._PROC_LOCK:
        rl._PROC = None
    rl._cierra_navegador()  # no debe lanzar


def test_finish_espera_a_quien_ya_comprueba(monkeypatch):
    """Quien llega segundo ESPERA al que comprueba y devuelve su resultado.

    Antes volvía al instante con «comprobando…» a medias, y el CLI convertía
    eso en un código de salida de fracaso para un login que estaba triunfando.
    """
    import threading

    llamadas = []
    monkeypatch.setattr(rl, "_finish_login", lambda: llamadas.append(1) or "corrió")

    rl.STATUS.set("checking", "en curso")
    rl._FINISH_LOCK.acquire()  # simula al vigilante comprobando

    def _suelta():
        rl.STATUS.set("ok", "sesión iniciada y guardada")
        rl._FINISH_LOCK.release()

    hilo = threading.Timer(0.15, _suelta)
    hilo.start()
    try:
        resultado = rl.finish_login()  # debe BLOQUEAR hasta que el otro acabe
    finally:
        hilo.join()
    assert resultado == "sesión iniciada y guardada"  # el resultado del otro
    assert llamadas == []                             # sin comprobar dos veces


def test_finish_no_repite_una_comprobacion_recien_hecha(monkeypatch):
    """Si el vigilante acaba de dar el ok, el botón/CLI no relanzan otro
    navegador para re-comprobar: un tropiezo puntual de DataDome convertiría
    un éxito real en «error»."""
    llamadas = []
    monkeypatch.setattr(rl, "_finish_login", lambda: llamadas.append(1) or "corrió")

    rl.STATUS.set("ok", "sesión iniciada y guardada")  # ahora mismo
    assert rl.finish_login() == "sesión iniciada y guardada"
    assert llamadas == []

    rl.STATUS.set("error", "algo falló")  # un estado no-ok sí se recomprueba
    assert rl.finish_login() == "corrió"
    assert llamadas == [1]


def _con_proc(proc):
    with rl._PROC_LOCK:
        rl._PROC = proc


def test_vigilante_remata_al_cerrarse_la_ventana(monkeypatch):
    """Cerrar la ventana del login dispara la comprobación sin el segundo clic."""
    llamadas = []
    monkeypatch.setattr(rl, "finish_login", lambda: llamadas.append(1))
    monkeypatch.setattr(rl, "WATCH_MIN_AGE_S", 0)
    monkeypatch.setattr(rl, "WATCH_POLL_S", 0)

    class _Proc:
        def __init__(self):
            self.n = 0

        def poll(self):
            self.n += 1
            return None if self.n < 2 else 0  # vivo, y al segundo sondeo cerrado

    proc = _Proc()
    _con_proc(proc)
    rl.STATUS.set("open", "abierto")
    rl._vigila_cierre(proc)
    assert llamadas == [1]


def test_vigilante_desconfia_de_una_muerte_temprana(monkeypatch):
    """Un navegador que muere al instante NO es un cierre humano: es un
    chrome.exe que reenvió la URL a otra instancia. Rematar ahí mataría la
    ventana donde la persona está tecleando su contraseña. Ante la duda, el
    vigilante se retira y queda el botón manual."""
    llamadas = []
    monkeypatch.setattr(rl, "finish_login", lambda: llamadas.append(1))
    monkeypatch.setattr(rl, "WATCH_MIN_AGE_S", 9999)  # todo cierre es "temprano"
    monkeypatch.setattr(rl, "WATCH_POLL_S", 0)

    class _Proc:
        def poll(self):
            return 0  # muerto desde el primer sondeo

    proc = _Proc()
    _con_proc(proc)
    rl.STATUS.set("open", "abierto")
    rl._vigila_cierre(proc)
    assert llamadas == []  # se retiró sin rematar


def test_vigilante_se_retira_si_otro_login_tomo_el_relevo(monkeypatch):
    """Si un segundo «iniciar sesión» reemplazó al navegador, el vigilante viejo
    sobra: rematar mataría la ventana del intento NUEVO. El guard de STATUS no
    basta, porque el estado vuelve a «open» enseguida."""
    llamadas = []
    monkeypatch.setattr(rl, "finish_login", lambda: llamadas.append(1))
    monkeypatch.setattr(rl, "WATCH_MIN_AGE_S", 0)
    monkeypatch.setattr(rl, "WATCH_POLL_S", 0)

    class _Proc:
        def poll(self):
            return 0  # el proc viejo está muerto (lo mató el relevo)

    _con_proc(object())  # _PROC ya es OTRO navegador
    rl.STATUS.set("open", "abierto (por el intento nuevo)")
    rl._vigila_cierre(_Proc())
    assert llamadas == []


def test_vigilante_se_retira_si_ya_se_comprobo(monkeypatch):
    """Si el botón manual ganó (STATUS ya no es 'open'), el vigilante no hace nada."""
    llamadas = []
    monkeypatch.setattr(rl, "finish_login", lambda: llamadas.append(1))
    monkeypatch.setattr(rl, "WATCH_POLL_S", 0)

    class _Proc:
        def poll(self):
            return None  # sigue vivo

    rl.STATUS.set("ok", "ya comprobado")
    rl._vigila_cierre(_Proc())
    assert llamadas == []


def test_matar_navegadores_solo_toca_el_perfil_indicado(monkeypatch, tmp_path):
    """Nunca debe cerrar el Chrome de siempre del usuario (otro user-data-dir)."""
    import psutil

    from app.ingest import live_base

    nuestro = tmp_path / "reuters"
    nuestro.mkdir()
    otro = tmp_path / "otro-perfil"

    matados: list[list[str]] = []

    class _Proc:
        def __init__(self, cmdline):
            self.info = {"cmdline": cmdline}

        def kill(self):
            matados.append(self.info["cmdline"])

    procesos = [
        _Proc(["chrome", f"--user-data-dir={nuestro}", "about:blank"]),  # nuestro → matar
        _Proc(["chrome", f"--user-data-dir={otro}", "x"]),               # otro perfil → respetar
        _Proc(["chrome", "https://web"]),                                # sin perfil → respetar
        _Proc(None),                                                     # sin cmdline → respetar
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procesos))
    monkeypatch.setattr(live_base.time, "sleep", lambda *_: None)

    n = live_base.matar_navegadores_del_perfil(nuestro)
    assert n == 1
    assert len(matados) == 1
    assert str(nuestro).lower() in " ".join(matados[0]).lower()
