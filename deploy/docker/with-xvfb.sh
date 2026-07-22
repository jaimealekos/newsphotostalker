#!/bin/sh
# Arranca Xvfb en :99 de forma robusta y ejecuta el comando que se le pasa.
#
# Por qué: tras un `docker restart` el /tmp del contenedor persiste, y el lock
# de X (`/tmp/.X99-lock`, `/tmp/.X11-unix/X99`) del arranque anterior hace que
# el nuevo `Xvfb :99` aborte y quede <defunct> -> sin DISPLAY -> el navegador
# de Reuters falla aunque uvicorn siga vivo. Limpiar los locks lo evita.
#   uso:  sh with-xvfb.sh <cmd> [args...]
rm -f /tmp/.X99-lock
rm -rf /tmp/.X11-unix/X99
Xvfb :99 -screen 0 "${XVFB_RES:-1600x1000x24}" -nolisten tcp &
export DISPLAY=:99
# Espera a que el socket de X exista antes de arrancar el comando principal.
i=0
while [ ! -S /tmp/.X11-unix/X99 ] && [ "$i" -lt 50 ]; do i=$((i+1)); sleep 0.1; done
exec "$@"
