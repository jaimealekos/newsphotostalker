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
import sys
import threading
import time
import webbrowser

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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Importar aquí y no arriba: al empaquetar, cargar la app es lo lento, y así
    # cualquier fallo de configuración sale ya con el log formateado.
    import uvicorn

    from app.config import BASE_DIR, get_settings
    # El objeto, no la cadena "app.main:app": empaquetado, resolver la cadena
    # depende de la maquinaria de importación de uvicorn y da menos sorpresas
    # pasarle la aplicación ya construida.
    from app.main import app as panel

    settings = get_settings()
    port = args.port or find_free_port(args.host)
    visible = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{visible}:{port}"

    print()
    print("  newsphotostalker")
    print(f"  panel:  {url}")
    print(f"  datos:  {BASE_DIR / 'data'}")
    print(f"  modo:   {settings.mode}")
    print()
    print("  Deja esta ventana abierta: al cerrarla se detiene el programa.")
    print()

    if not args.sin_navegador:
        threading.Thread(
            target=open_browser_when_ready, args=(url, visible, port), daemon=True
        ).start()

    try:
        uvicorn.run(panel, host=args.host, port=port, log_level="info")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
