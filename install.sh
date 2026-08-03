#!/usr/bin/env sh
# Instalador de newsphotostalker para Linux y macOS.
#
#     curl -fsSL https://raw.githubusercontent.com/jaimealekos/newsphotostalker/main/install.sh | sh
#
# Baja el paquete de la última versión, lo deja en ~/.local/share y crea el
# mandato `newsphotostalker` en ~/.local/bin. No pide permisos de root, no toca
# nada del sistema y no necesita Python: el programa lo trae dentro.
#
# Para desinstalar: borra esas dos cosas.
#     rm -rf ~/.local/share/newsphotostalker ~/.local/bin/newsphotostalker
set -eu

REPO="jaimealekos/newsphotostalker"
DESTINO="${HOME}/.local/share/newsphotostalker"
BIN="${HOME}/.local/bin"

case "$(uname -s)" in
  Linux)  SISTEMA=linux ;;
  Darwin) SISTEMA=macos ;;
  *) echo "Sistema no soportado: $(uname -s). En Windows, descarga el .zip a mano." >&2; exit 1 ;;
esac

for orden in curl unzip; do
  command -v "$orden" >/dev/null 2>&1 || { echo "Falta '$orden'. Instálalo y repite." >&2; exit 1; }
done

echo "Buscando la última versión de ${REPO}..."
URL=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
  | grep -o "https://[^\"]*-${SISTEMA}-[^\"]*\.zip" | head -1)
[ -n "${URL}" ] || { echo "No hay paquete para ${SISTEMA} en la última release." >&2; exit 1; }

TEMPORAL=$(mktemp -d)
trap 'rm -rf "${TEMPORAL}"' EXIT
echo "Descargando ${URL##*/}..."
curl -fL# "${URL}" -o "${TEMPORAL}/paquete.zip"

echo "Instalando en ${DESTINO}..."
rm -rf "${DESTINO}"
mkdir -p "${DESTINO}" "${BIN}"
unzip -q "${TEMPORAL}/paquete.zip" -d "${TEMPORAL}/extraido"
mv "${TEMPORAL}/extraido/newsphotostalker" "${DESTINO}/app"
chmod +x "${DESTINO}/app/newsphotostalker"

# macOS marca en cuarentena lo descargado y se niega a abrirlo; el programa no
# va firmado, así que se le quita la marca aquí y el usuario no se topa con ello.
if [ "${SISTEMA}" = macos ]; then
  xattr -dr com.apple.quarantine "${DESTINO}/app" 2>/dev/null || true
fi

# Los datos viven junto al ejecutable (dentro de app/data), así que el lanzador
# entra ahí antes de arrancar: si un día mueves la carpeta, se lleva todo.
cat > "${BIN}/newsphotostalker" <<LANZADOR
#!/usr/bin/env sh
cd "${DESTINO}/app" && exec ./newsphotostalker "\$@"
LANZADOR
chmod +x "${BIN}/newsphotostalker"

echo
echo "Listo. Arráncalo con:"
echo "    newsphotostalker"
echo
echo "  En un servidor sin pantalla:"
echo "    newsphotostalker --host 0.0.0.0 --sin-navegador"
echo
echo "  Entra con  admin / admin  y cámbialo en ajustes."
echo "  Tus fotos y tu base de datos quedan en ${DESTINO}/app/data"
case ":${PATH}:" in
  *":${BIN}:"*) ;;
  *) echo; echo "  OJO: ${BIN} no está en tu PATH. Añádelo a tu ~/.profile:";
     echo "      export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
