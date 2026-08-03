"""Genera el paquete de este sistema, listo para subir a GitHub.

    python build.py

Deja en ``dist/`` la carpeta ``newsphotostalker/`` y un
``newsphotostalker-<sistema>-<version>.zip`` con ella dentro: eso es lo que se
adjunta a una release. El usuario descomprime y ejecuta.

Compila para el sistema en el que se ejecuta y solo para ese: los binarios no se
pueden compilar cruzados. De ahí que Windows, macOS y Linux salgan cada uno de
su propio runner en GitHub Actions.

Se compila en modo *onedir* (una carpeta, no un fichero único). El porqué está
explicado en ``newsphotostalker.spec``.

Opciones útiles al desarrollar:

    python build.py --salida C:/tmp/dist   # compilar fuera del repositorio
    python build.py --sin-zip              # solo la carpeta, sin comprimir
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
NOMBRE = "newsphotostalker"
#: En Windows el binario lleva extensión; en macOS y Linux, no.
EJECUTABLE = f"{NOMBRE}.exe" if sys.platform == "win32" else NOMBRE
#: Chromium empaquetado (macOS y Linux), como tarball. Ver preparar_chromium().
TARBALL = RAIZ / "assets" / "chromium.tar.gz"


def preparar_chromium() -> None:
    """Empaqueta el Chromium de Playwright en un .tar.gz para meterlo dentro.

    Por qué un tarball y no la carpeta tal cual, que sería lo obvio:

    * en macOS, PyInstaller intenta **re-firmar** todo binario Mach-O que
      encuentra entre los datos, y con Chromium.app falla (`codesign … failed`);
    * y el tar conserva el **bit de ejecución**, que PyInstaller pierde al
      copiar datos, así que el navegador llegaría sin permisos y no arrancaría.

    En Windows no se hace: siempre hay Edge de serie y son ~150 MB de más.
    """
    TARBALL.unlink(missing_ok=True)
    if sys.platform == "win32" or os.environ.get("NPS_SIN_CHROMIUM"):
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


def version() -> str:
    texto = (RAIZ / "app" / "__init__.py").read_text(encoding="utf-8")
    for linea in texto.splitlines():
        if linea.startswith("__version__"):
            return linea.split("=")[1].strip().strip('"').strip("'")
    return "0.0.0"


def compilar(salida: Path, trabajo: Path) -> Path:
    preparar_chromium()
    print(f"== compilando {NOMBRE} {version()} ==")
    orden = [
        sys.executable, "-m", "PyInstaller", str(RAIZ / f"{NOMBRE}.spec"),
        "--noconfirm", "--distpath", str(salida), "--workpath", str(trabajo),
    ]
    proceso = subprocess.run(orden, cwd=RAIZ)
    if proceso.returncode != 0:
        raise SystemExit("PyInstaller ha fallado; revisa la salida de arriba")
    carpeta = salida / NOMBRE
    if not (carpeta / EJECUTABLE).exists():
        raise SystemExit(f"no se ha generado {carpeta / EJECUTABLE}")
    return carpeta


#: Nombre del sistema en el fichero final. install.sh busca justo estas palabras.
SISTEMA = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


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
    print("\nListo. Para probarlo, ejecuta el .exe de esa carpeta.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
