"""Genera el paquete de este sistema, listo para subir a GitHub.

    python build.py

Deja en ``dist/`` la carpeta ``newsphotostalker/`` y un
``newsphotostalker-<sistema>-<version>.zip`` con ella dentro: eso es lo que se
adjunta a una release. El usuario descomprime y ejecuta.

Compila para el sistema en el que se ejecuta y solo para ese, porque nada de
esto se puede compilar cruzado. Cada sistema sale de su propio runner en
GitHub Actions.

Dos formas de empaquetar, según el sistema:

* **Windows → versión portable**: un Python embebido (el de python.org) que un
  ``.bat`` arranca sobre el código. No se usa PyInstaller.
* **macOS y Linux → PyInstaller**: un ejecutable de un archivo por carpeta
  (*onedir*). Ver ``newsphotostalker.spec``.

Opciones útiles al desarrollar:

    python build.py --salida C:/tmp/dist   # compilar fuera del repositorio
    python build.py --sin-zip              # solo la carpeta, sin comprimir
    python build.py --solo-zip             # comprimir una carpeta ya hecha
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
NOMBRE = "newsphotostalker"
WIN = sys.platform == "win32"

#: El lanzador que verá el usuario, y que se comprueba tras compilar.
LANZADOR = f"{NOMBRE}.bat" if WIN else NOMBRE

#: Nombre del sistema en el fichero final. install.sh busca justo estas palabras.
SISTEMA = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")

#: Chromium empaquetado (macOS y Linux), como tarball. Ver preparar_chromium().
TARBALL = RAIZ / "assets" / "chromium.tar.gz"

# --- versión portable de Windows -------------------------------------------
#: Python embebido que se empaqueta. Fijo, para que el build sea reproducible.
PY_EMBED_VERSION = "3.12.10"
PY_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PY_EMBED_VERSION}/"
    f"python-{PY_EMBED_VERSION}-embed-amd64.zip"
)
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

BAT = """\
@echo off
title newsphotostalker
cd /d "%~dp0"
set "NPS_PORTABLE=1"
"%~dp0python\\python.exe" "%~dp0run.py" %*
echo.
echo El programa se ha detenido. Pulsa una tecla para cerrar esta ventana.
pause >nul
"""

#: Lanzador de macOS. Un ejecutable de Unix sin extensión se puede abrir con
#: doble clic, pero es frágil: si el desempaquetado pierde el bit de ejecución,
#: el Finder deja de verlo como programa y se limita a abrir una ventana de
#: Terminal vacía, sin un solo mensaje que explique nada. Un ``.command`` sí
#: tiene extensión conocida, siempre lo ejecuta Terminal, y de paso puede
#: arreglar lo que el .zip haya estropeado antes de arrancar.
COMMAND = """\
#!/bin/sh
# Doble clic aquí. Esta ventana ES el programa: al cerrarla, se para.
cd "$(dirname "$0")" || exit 1

# macOS pone en cuarentena TODO lo que sale de un .zip descargado: no solo lo
# que abres, también las bibliotecas de dentro y el navegador que viaja con el
# programa. Basta con que una pieza siga marcada para que el arranque muera
# —a veces en silencio— aunque hayas dado permiso al lanzador. Esto lo limpia
# de una vez, y no pide contraseña: son ficheros tuyos.
xattr -dr com.apple.quarantine . 2>/dev/null

# Y el bit de ejecución, que algunos desempaquetadores se dejan por el camino.
chmod +x ./newsphotostalker 2>/dev/null

./newsphotostalker "$@"
estado=$?

# Si algo falla, la ventana NO se cierra: el mensaje de error es justo lo que
# hace falta para arreglarlo, y cerrarla se lo lleva por delante.
if [ "$estado" -ne 0 ]; then
  echo
  echo "----------------------------------------------------------------"
  echo "newsphotostalker ha terminado con el error $estado."
  echo "Copia el texto de arriba: es lo que hace falta para diagnosticarlo."
  echo "----------------------------------------------------------------"
  printf "Pulsa Intro para cerrar esta ventana. "
  read -r _
fi
"""


def _descarga(url: str, destino: Path) -> None:
    print(f"   bajando {url.rsplit('/', 1)[-1]}")
    with urllib.request.urlopen(url) as respuesta, open(destino, "wb") as fichero:
        shutil.copyfileobj(respuesta, fichero)


def build_portable_windows(salida: Path) -> Path:
    """Monta la versión portable: Python embebido + dependencias + código + .bat.

    El resultado se ejecuta con el ``python.exe`` embebido sobre el código, no con
    un ejecutable propio.
    """
    dest = salida / NOMBRE
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    py = dest / "python"
    py.mkdir()

    trabajo = salida / "_portable_tmp"
    shutil.rmtree(trabajo, ignore_errors=True)
    trabajo.mkdir(parents=True)
    try:
        print(f"== Python embebido {PY_EMBED_VERSION} ==")
        embed = trabajo / "embed.zip"
        _descarga(PY_EMBED_URL, embed)
        with zipfile.ZipFile(embed) as zf:
            zf.extractall(py)

        # El embebido va en modo aislado: se le habilita ``site`` y se le añade
        # site-packages, para que pip instale ahí y los imports lo encuentren.
        pth = next(py.glob("python*._pth"))
        lineas = pth.read_text(encoding="utf-8").splitlines()
        lineas = ["import site" if x.strip() == "#import site" else x for x in lineas]
        if "Lib\\site-packages" not in lineas:
            lineas.append("Lib\\site-packages")
        pth.write_text("\n".join(lineas) + "\n", encoding="utf-8")

        exe = py / "python.exe"
        print("== pip ==")
        get_pip = trabajo / "get-pip.py"
        _descarga(GET_PIP_URL, get_pip)
        _corre([str(exe), str(get_pip), "--no-warn-script-location"])

        print("== dependencias ==")
        _corre([
            str(exe), "-m", "pip", "install", "--no-warn-script-location",
            "-r", str(RAIZ / "requirements.txt"),
        ])
        # pytest solo se usa en las pruebas; fuera del paquete que se descarga.
        _corre([str(exe), "-m", "pip", "uninstall", "-y", "pytest"], tolera_fallo=True)
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)

    print("== código y lanzador ==")
    shutil.copytree(RAIZ / "app", dest / "app", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(RAIZ / "run.py", dest / "run.py")
    shutil.copy2(RAIZ / "config.example.yaml", dest / "config.example.yaml")
    (dest / f"{NOMBRE}.bat").write_text(BAT, encoding="utf-8")
    return dest


def _corre(orden: list[str], tolera_fallo: bool = False) -> None:
    proceso = subprocess.run(orden, cwd=RAIZ)
    if proceso.returncode != 0 and not tolera_fallo:
        raise SystemExit(f"falló: {' '.join(orden[:3])}…")


# --- versión PyInstaller (macOS y Linux) -----------------------------------
def preparar_chromium() -> None:
    """Empaqueta el Chromium de Playwright en un .tar.gz para meterlo dentro.

    Por qué un tarball y no la carpeta tal cual, que sería lo obvio:

    * en macOS, PyInstaller intenta **re-firmar** todo binario Mach-O que
      encuentra entre los datos, y con Chromium.app falla (`codesign … failed`);
    * y el tar conserva el **bit de ejecución**, que PyInstaller pierde al
      copiar datos, así que el navegador llegaría sin permisos y no arrancaría.
    """
    TARBALL.unlink(missing_ok=True)
    if WIN or os.environ.get("NPS_SIN_CHROMIUM"):
        return
    cache = {
        "darwin": Path.home() / "Library" / "Caches" / "ms-playwright",
        "linux": Path.home() / ".cache" / "ms-playwright",
    }[sys.platform if sys.platform == "darwin" else "linux"]
    # Ojo con el patrón: Playwright instala DOS paquetes, `chromium-<n>` para el
    # modo con ventana y `chromium_headless_shell-<n>` para el headless, que es
    # justo el que usan las búsquedas. Con solo el primero, el programa arranca
    # pero no busca nada.
    navegadores = sorted(cache.glob("chromium*")) if cache.is_dir() else []
    if not navegadores:
        print("aviso: no hay Chromium de Playwright que empaquetar "
              "(ejecuta 'python -m playwright install chromium')")
        return
    TARBALL.parent.mkdir(parents=True, exist_ok=True)
    print(f"== empaquetando {len(navegadores)} navegador(es) en {TARBALL.name} ==")
    with tarfile.open(TARBALL, "w:gz", compresslevel=6) as tar:
        for carpeta in navegadores:
            tar.add(carpeta, arcname=carpeta.name)
    print(f"   {TARBALL.stat().st_size / 1024 / 1024:.0f} MB")


def build_pyinstaller(salida: Path, trabajo: Path) -> Path:
    preparar_chromium()
    orden = [
        sys.executable, "-m", "PyInstaller", str(RAIZ / f"{NOMBRE}.spec"),
        "--noconfirm", "--distpath", str(salida), "--workpath", str(trabajo),
    ]
    if subprocess.run(orden, cwd=RAIZ).returncode != 0:
        raise SystemExit("PyInstaller ha fallado; revisa la salida de arriba")
    carpeta = salida / NOMBRE
    if sys.platform == "darwin":
        lanzador = carpeta / f"{NOMBRE}.command"
        lanzador.write_text(COMMAND, encoding="utf-8")
        lanzador.chmod(0o755)
        print(f"== lanzador de macOS: {lanzador.name} ==")
    return carpeta


# --- común ------------------------------------------------------------------
def version() -> str:
    texto = (RAIZ / "app" / "__init__.py").read_text(encoding="utf-8")
    for linea in texto.splitlines():
        if linea.startswith("__version__"):
            return linea.split("=")[1].strip().strip('"').strip("'")
    return "0.0.0"


def compilar(salida: Path, trabajo: Path) -> Path:
    print(f"== compilando {NOMBRE} {version()} para {SISTEMA} ==")
    carpeta = build_portable_windows(salida) if WIN else build_pyinstaller(salida, trabajo)
    if not (carpeta / LANZADOR).exists():
        raise SystemExit(f"no se ha generado {carpeta / LANZADOR}")
    return carpeta


def comprimir(carpeta: Path) -> Path:
    destino = carpeta.parent / f"{NOMBRE}-{SISTEMA}-{version()}.zip"
    destino.unlink(missing_ok=True)
    print(f"== comprimiendo -> {destino.name} ==")
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for fichero in sorted(carpeta.rglob("*")):
            if fichero.is_file():
                zf.write(fichero, fichero.relative_to(carpeta.parent))
    return destino


def tamano(ruta: Path) -> str:
    if ruta.is_file():
        total = ruta.stat().st_size
    else:
        total = sum(f.stat().st_size for f in ruta.rglob("*") if f.is_file())
    return f"{total / 1024 / 1024:.0f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salida", default=str(RAIZ / "dist"), help="carpeta de salida")
    parser.add_argument("--trabajo", default=str(RAIZ / "build"), help="carpeta temporal")
    parser.add_argument("--sin-zip", action="store_true", help="compilar sin comprimir")
    parser.add_argument(
        "--solo-zip",
        action="store_true",
        help="comprimir una carpeta ya compilada, sin volver a compilar",
    )
    args = parser.parse_args()

    salida = Path(args.salida).resolve()
    carpeta = salida / NOMBRE
    if args.solo_zip:
        if not carpeta.exists():
            raise SystemExit(f"no existe {carpeta}; compila primero")
    else:
        shutil.rmtree(carpeta, ignore_errors=True)
        carpeta = compilar(salida, Path(args.trabajo).resolve())
        print(f"\ncarpeta: {carpeta}  ({tamano(carpeta)})")
    if not args.sin_zip:
        zip_final = comprimir(carpeta)
        print(f"zip:     {zip_final}  ({tamano(zip_final)})")
    arranque = LANZADOR if WIN else f"./{LANZADOR}"
    print(f"\nListo. Para probarlo, ejecuta {arranque} de esa carpeta.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
