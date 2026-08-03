# -*- mode: python ; coding: utf-8 -*-
"""Empaquetado de newsphotostalker para Windows (PyInstaller, modo onedir).

Se genera una CARPETA, no un fichero único, a propósito:

* arranca mucho más rápido (un onefile se descomprime entero en cada arranque);
* Playwright arrastra un Node y su directorio `driver` (~87 MB) que en onefile
  hay que reextraer y reapuntar en cada ejecución, y es la fuente clásica de
  fallos raros;
* y sobre todo, en onefile `__file__` apunta a una carpeta temporal que Windows
  borra al salir: los datos del usuario no pueden vivir ahí.

El usuario descomprime el .zip y hace doble clic en newsphotostalker.exe. Sus
fotos, su base de datos y su config.local.yaml se crean AL LADO del .exe.

    python -m PyInstaller newsphotostalker.spec --noconfirm
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

# Playwright: hay que llevarse el paquete entero, con su driver (node + JS).
pw_datas, pw_binaries, pw_hidden = collect_all("playwright")

# Y, fuera de Windows, también su Chromium si está descargado.
#
# En Windows no hace falta: siempre hay Edge de serie, y el Chromium de
# Playwright además no arranca headed allí. En macOS y Linux puede no haber
# ningún navegador instalado, así que se mete dentro para que el programa
# funcione nada más descargarlo. El arranque lo encuentra porque
# app/config.py apunta PLAYWRIGHT_BROWSERS_PATH al propio paquete.
BROWSERS_DIR = {
    "darwin": Path.home() / "Library" / "Caches" / "ms-playwright",
    "linux": Path.home() / ".cache" / "ms-playwright",
}.get(sys.platform if sys.platform != "win32" else "", None)

if BROWSERS_DIR and BROWSERS_DIR.is_dir() and not os.environ.get("NPS_SIN_CHROMIUM"):
    for hijo in BROWSERS_DIR.iterdir():
        if hijo.is_dir() and hijo.name.startswith("chromium"):
            pw_datas.append((str(hijo), f"ms-playwright/{hijo.name}"))

hiddenimports = [
    *pw_hidden,
    # uvicorn resuelve sus protocolos y bucles por nombre en tiempo de
    # ejecución, así que el analizador estático no los ve venir.
    *collect_submodules("uvicorn"),
    # Los almacenes y disparadores de APScheduler se cargan por punto de entrada.
    *collect_submodules("apscheduler"),
    # SQLAlchemy elige el dialecto por la URL de conexión.
    "sqlalchemy.dialects.sqlite",
]

datas = [
    *pw_datas,
    # Plantillas y estáticos: se sirven desde BUNDLE_DIR (ver app/config.py),
    # por eso conservan la ruta app/… dentro del paquete.
    ("app/templates", "app/templates"),
    ("app/static", "app/static"),
    # Config de referencia. La del usuario se escribe junto al .exe en el
    # primer arranque; esta es solo el respaldo de solo lectura.
    ("config.example.yaml", "."),
]

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=pw_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Fuera lo que no usa el programa pero arrastran las dependencias.
    excludes=["tkinter", "matplotlib", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="newsphotostalker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # CON consola, y es deliberado: la ventana ES el programa. Ahí se ve el log
    # y cerrarla detiene el servidor, sin necesidad de icono de bandeja.
    console=True,
    icon="assets/newsphotostalker.ico",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="newsphotostalker",
)
