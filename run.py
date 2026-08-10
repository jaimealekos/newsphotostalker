"""Punto de entrada de newsphotostalker: arranca el servidor y abre el panel.

Es lo que ejecuta el .exe empaquetado y también sirve en desarrollo::

    python run.py

Se queda corriendo mientras la ventana esté abierta; al cerrarla o con Ctrl+C se
para el servidor. Todo lo demás (fotos, base de datos, sesión del navegador) se
crea en la carpeta ``data/`` junto al ejecutable.
"""

from __future__ import annotations

import argparse
import logging
import socket
import subprocess  # noqa: F401 - lo usa avisar() en macOS
import sys
import threading
import time
import webbrowser
from pathlib import Path

# La carpeta de este fichero, en sys.path, para poder importar ``app``. Hace
# falta con el Python embebido de la versión portable de Windows: va en modo
# aislado (su ``._pth``) y no añade el directorio del script por su cuenta, así
# que sin esto ``from app.main import app`` falla. En una instalación normal ya
# está, pero repetirlo no molesta.
sys.path.insert(0, str(Path(__file__).resolve().parent))

HOST = "127.0.0.1"
#: Primer puerto que se intenta; si está ocupado se prueban los siguientes.
PREFERRED_PORT = 8010
PORT_ATTEMPTS = 20


def find_free_port(host: str = HOST, first: int = PREFERRED_PORT) -> int:
    """Primer puerto libre a partir de ``first``.

    Que el puerto esté ocupado es lo normal si ya tienes una copia abierta o
    cualquier otra cosa escuchando; mejor moverse que morir con un traceback.
    """
    for port in range(first, first + PORT_ATTEMPTS):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    # Que lo elija el sistema antes que rendirse.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def open_browser_when_ready(url: str, host: str, port: int, timeout: float = 25.0) -> None:
    """Abre el navegador en cuanto el servidor acepta conexiones."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            if sock.connect_ex((host, port)) == 0:
                break
        time.sleep(0.25)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - sin navegador, la URL ya está impresa
        pass


def parse_args(argv: list[str] | None = None):
    """Opciones de consola. En un servidor no hay navegador que abrir ni tiene
    por qué escuchar solo en local, así que ambas cosas se pueden cambiar."""
    parser = argparse.ArgumentParser(
        prog="newsphotostalker",
        description="Vigila el trabajo de otros fotógrafos en AP, Reuters, AFP y Getty.",
    )
    parser.add_argument("--host", default=HOST, help=f"dirección de escucha (por defecto {HOST})")
    parser.add_argument(
        "--port", type=int, default=None,
        help=f"puerto fijo; si no, el primero libre desde el {PREFERRED_PORT}",
    )
    parser.add_argument(
        "--sin-navegador", action="store_true",
        help="no abrir el navegador al arrancar (servidores sin pantalla)",
    )
    parser.add_argument(
        "--sin-icono", action="store_true",
        help="no poner el icono junto al reloj; el programa vive en esta consola",
    )
    return parser.parse_args(argv)


def ya_esta_en_marcha(host: str, port: int) -> bool:
    """¿Hay ya una copia de ESTE programa escuchando ahí?

    Sin esto, el segundo doble clic levantaba un servidor más en otro puerto:
    dos programas descargando a la vez sobre la misma carpeta. Ahora el segundo
    se limita a abrir el panel del primero.
    """
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/login", timeout=2) as respuesta:
            return respuesta.status == 200 and b"newsphotostalker" in respuesta.read(8000).lower()
    except Exception:  # noqa: BLE001 - no hay nadie, o no es nuestro
        return False


def avisar(mensaje: str) -> None:
    """Enseña un aviso del sistema. Sin consola, es la única forma de que un
    fallo al arrancar no sea completamente mudo."""
    print(mensaje)
    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, mensaje, "newsphotostalker", 0x10)
        elif sys.platform == "darwin":
            import subprocess

            subprocess.run(
                ["osascript", "-e",
                 f'display dialog {mensaje!r} with title "newsphotostalker" '
                 'buttons {"Cerrar"} with icon caution'],
                capture_output=True,
            )
    except Exception:  # noqa: BLE001 - avisar nunca puede empeorar las cosas
        pass


def monta_registro(base_dir: Path) -> None:
    """Log por pantalla y, además, a un fichero.

    El fichero no es un lujo: arrancando sin consola (que es lo normal desde que
    el programa vive en el icono junto al reloj) la salida estándar no va a
    ninguna parte, y sin esto un fallo no dejaría ni rastro que mirar.
    """
    formato = logging.Formatter(
        "%(asctime)s  %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S"
    )
    manejadores: list[logging.Handler] = []
    try:
        destino = base_dir / "data"
        destino.mkdir(parents=True, exist_ok=True)
        fichero = logging.FileHandler(destino / "newsphotostalker.log", encoding="utf-8")
        fichero.setFormatter(formato)
        manejadores.append(fichero)
    except OSError:
        pass  # sin sitio donde escribir; al menos que quede la consola
    if sys.stdout is not None:  # con pythonw.exe no hay salida estándar
        consola = logging.StreamHandler()
        consola.setFormatter(formato)
        manejadores.append(consola)

    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    for manejador in manejadores:
        raiz.addHandler(manejador)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Importar aquí y no arriba: al empaquetar, cargar la app es lo lento, y así
    # cualquier fallo de configuración sale ya con el log formateado.
    import uvicorn

    from app import bandeja
    from app.config import BASE_DIR, get_settings

    monta_registro(BASE_DIR)
    # El objeto, no la cadena "app.main:app": empaquetado, resolver la cadena
    # depende de la maquinaria de importación de uvicorn y da menos sorpresas
    # pasarle la aplicación ya construida.
    from app.main import app as panel

    settings = get_settings()
    visible = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host

    # ¿Doble clic sobre algo que ya estaba funcionando? Abrir el panel y ya.
    if args.port is None and ya_esta_en_marcha(visible, PREFERRED_PORT):
        url = f"http://{visible}:{PREFERRED_PORT}"
        print(f"  newsphotostalker ya está en marcha: {url}")
        if not args.sin_navegador:
            webbrowser.open(url)
        return 0

    port = args.port or find_free_port(args.host)
    url = f"http://{visible}:{port}"
    con_icono = not args.sin_icono and not args.sin_navegador and bandeja.disponible()

    print()
    print("  newsphotostalker")
    print(f"  panel:  {url}")
    print(f"  datos:  {BASE_DIR / 'data'}")
    print(f"  modo:   {settings.mode}")
    print()
    if con_icono:
        print("  Vive en el icono junto al reloj. Ahí se abre el panel y se sale.")
    else:
        print("  Deja esta ventana abierta: al cerrarla se detiene el programa.")
    print()

    if not args.sin_navegador:
        threading.Thread(
            target=open_browser_when_ready, args=(url, visible, port), daemon=True
        ).start()

    if not con_icono:
        # Modo consola de siempre: el servidor manda y la ventana es el programa.
        try:
            uvicorn.run(panel, host=args.host, port=port, log_level="info")
        except KeyboardInterrupt:
            pass
        return 0

    # Con icono, se reparten: el icono se queda el hilo principal (en macOS la
    # barra de menús solo se maneja desde ahí) y el servidor corre en otro.
    servidor = uvicorn.Server(uvicorn.Config(panel, host=args.host, port=port, log_level="info"))
    hilo = threading.Thread(target=servidor.run, name="uvicorn", daemon=True)
    hilo.start()

    # Esperar a que levante de verdad. Si no lo hace, decirlo: sin consola a la
    # vista, un fallo mudo deja al usuario mirando una pantalla que no pasa nada.
    if not espera_a_que_arranque(servidor, hilo):
        avisar(
            "newsphotostalker no ha podido arrancar.\n\n"
            f"Mira el detalle en:\n{BASE_DIR / 'data' / 'newsphotostalker.log'}"
        )
        return 1

    def parar():
        servidor.should_exit = True
        hilo.join(timeout=15)

    bandeja.arrancar(url, parar)
    return 0


def espera_a_que_arranque(servidor, hilo, timeout: float = 40.0) -> bool:
    """True cuando el servidor acepta conexiones; False si murió por el camino."""
    limite = time.time() + timeout
    while time.time() < limite:
        if getattr(servidor, "started", False):
            return True
        if not hilo.is_alive():
            return False
        time.sleep(0.2)
    return False


if __name__ == "__main__":
    sys.exit(main())
