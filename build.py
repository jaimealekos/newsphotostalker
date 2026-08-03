"""Genera el paquete de Windows listo para subir a GitHub.

    python build.py

Deja en ``dist/`` la carpeta ``newsphotostalker/`` y un
``newsphotostalker-windows-<version>.zip`` con ella dentro: eso es lo que se
adjunta a una release. El usuario descomprime y hace doble clic en el .exe.

Se compila en modo *onedir* (una carpeta, no un fichero único). El porqué está
explicado en ``newsphotostalker.spec``.

Opciones útiles al desarrollar:

    python build.py --salida C:/tmp/dist   # compilar fuera del repositorio
    python build.py --sin-zip              # solo la carpeta, sin comprimir
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
NOMBRE = "newsphotostalker"


def version() -> str:
    texto = (RAIZ / "app" / "__init__.py").read_text(encoding="utf-8")
    for linea in texto.splitlines():
        if linea.startswith("__version__"):
            return linea.split("=")[1].strip().strip('"').strip("'")
    return "0.0.0"


def compilar(salida: Path, trabajo: Path) -> Path:
    print(f"== compilando {NOMBRE} {version()} ==")
    orden = [
        sys.executable, "-m", "PyInstaller", str(RAIZ / f"{NOMBRE}.spec"),
        "--noconfirm", "--distpath", str(salida), "--workpath", str(trabajo),
    ]
    proceso = subprocess.run(orden, cwd=RAIZ)
    if proceso.returncode != 0:
        raise SystemExit("PyInstaller ha fallado; revisa la salida de arriba")
    carpeta = salida / NOMBRE
    if not (carpeta / f"{NOMBRE}.exe").exists():
        raise SystemExit(f"no se ha generado {carpeta / (NOMBRE + '.exe')}")
    return carpeta


def comprimir(carpeta: Path) -> Path:
    destino = carpeta.parent / f"{NOMBRE}-windows-{version()}.zip"
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
