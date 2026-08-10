"""El arranque sin consola: icono, copia única y fallos que no son mudos.

Desde que el programa vive en el icono junto al reloj, la ventana de consola ya
no está para contar lo que pasa. Eso obliga a que tres cosas funcionen bien, y
son las que se prueban aquí.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

import run
from app import bandeja


# --- ¿hay ya una copia funcionando? ----------------------------------------
def test_detecta_que_no_hay_nadie_escuchando():
    """Un puerto vacío no puede confundirse con una copia en marcha."""
    assert run.ya_esta_en_marcha("127.0.0.1", 9) is False


def test_no_confunde_a_otro_programa_con_el_nuestro(monkeypatch):
    """Otro servidor en ese puerto no es esto: arrancar encima seria peor.

    Sin esta comprobacion, el segundo doble clic levantaba OTRO servidor sobre
    la misma carpeta de datos: dos programas descargando a la vez.
    """
    class RespuestaAjena:
        status = 200

        def read(self, _n=0):
            return b"<html>otro servidor cualquiera</html>"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: RespuestaAjena())
    assert run.ya_esta_en_marcha("127.0.0.1", 1234) is False


def test_reconoce_nuestro_propio_panel(monkeypatch):
    class RespuestaPropia:
        status = 200

        def read(self, _n=0):
            return b"<title>newsphotostalker</title>"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: RespuestaPropia())
    assert run.ya_esta_en_marcha("127.0.0.1", 1234) is True


# --- el registro a fichero --------------------------------------------------
def test_el_log_va_a_fichero_aunque_no_haya_consola(tmp_path, monkeypatch):
    """Con pythonw.exe no hay salida estándar: si no se escribe a fichero, un
    fallo no deja ni rastro que mirar."""
    raiz = logging.getLogger()
    previos = raiz.handlers[:]
    try:
        raiz.handlers.clear()
        monkeypatch.setattr(run.sys, "stdout", None)   # como bajo pythonw.exe
        run.monta_registro(tmp_path)
        logging.getLogger("prueba").warning("algo que contar")
        for manejador in raiz.handlers:
            manejador.flush()
        registro = tmp_path / "data" / "newsphotostalker.log"
        assert registro.is_file()
        assert "algo que contar" in registro.read_text(encoding="utf-8")
    finally:
        for manejador in raiz.handlers:
            manejador.close()
        raiz.handlers[:] = previos


# --- esperar a que el servidor levante --------------------------------------
class _ServidorFalso:
    def __init__(self, started):
        self.started = started


def test_espera_hasta_que_el_servidor_esta_listo():
    hilo = threading.Thread(target=lambda: time.sleep(2), daemon=True)
    hilo.start()
    assert run.espera_a_que_arranque(_ServidorFalso(True), hilo, timeout=2) is True


def test_si_el_servidor_muere_no_se_queda_esperando():
    """Antes esto habria dejado al usuario ante una pantalla donde no pasa nada;
    ahora devuelve False y el programa saca un aviso del sistema."""
    hilo = threading.Thread(target=lambda: None)
    hilo.start()
    hilo.join()
    assert run.espera_a_que_arranque(_ServidorFalso(False), hilo, timeout=5) is False


# --- el icono ---------------------------------------------------------------
def test_en_linux_no_se_intenta_poner_icono(monkeypatch):
    """Media docena de escritorios con bandejas distintas, y alli esto se usa
    sobre todo en servidores sin pantalla."""
    monkeypatch.setattr(bandeja.sys, "platform", "linux")
    assert bandeja.disponible() is False


def test_sin_la_biblioteca_del_icono_el_programa_sigue_arrancando(monkeypatch):
    """El icono es un adorno: si falta, se cae al modo consola, no se muere."""
    monkeypatch.setattr(bandeja.sys, "platform", "win32")
    import builtins

    real = builtins.__import__

    def sin_pystray(nombre, *a, **kw):
        if nombre == "pystray":
            raise ImportError("no está")
        return real(nombre, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", sin_pystray)
    assert bandeja.disponible() is False


def test_el_icono_tiene_imagen():
    """Se dibuja una de emergencia si el fichero no estuviera, para que un icono
    ausente no impida arrancar."""
    imagen = bandeja._imagen()
    assert imagen.size == (bandeja.LADO, bandeja.LADO)


@pytest.mark.parametrize(
    "argumentos, espera_icono",
    [([], True), (["--sin-icono"], False), (["--sin-navegador"], False)],
)
def test_las_opciones_de_consola_deciden_si_hay_icono(argumentos, espera_icono):
    """`--sin-navegador` es para servidores sin pantalla: alli un icono no pinta
    nada, asi que tampoco se intenta."""
    args = run.parse_args(argumentos)
    quiere = not args.sin_icono and not args.sin_navegador
    assert quiere is espera_icono
