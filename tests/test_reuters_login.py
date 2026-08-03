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
