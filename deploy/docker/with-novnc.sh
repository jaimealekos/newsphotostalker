#!/bin/sh
# Escritorio virtual mínimo para hacer el login de Reuters en un servidor SIN
# pantalla: Xvfb + un gestor de ventanas + noVNC, y encima el login.
#
# Lo arranca el CMD de Dockerfile.vnc.
#
#   sh with-novnc.sh                 -> el login de Reuters (uso normal)
#   sh with-novnc.sh <cmd> [args]    -> otra cosa sobre el mismo escritorio,
#                                       útil para diagnosticar
set -e

# 1. Xvfb. Se limpian antes los locks de X de un arranque anterior: sin esto,
#    tras un `docker restart` Xvfb aborta y queda <defunct>, y lo que se ve en
#    noVNC es una pantalla NEGRA sin ningún mensaje que lo explique.
rm -f /tmp/.X99-lock
rm -rf /tmp/.X11-unix/X99
Xvfb :99 -screen 0 "${XVFB_RES:-1440x900x24}" -nolisten tcp &
export DISPLAY=:99

# 2. Esperar a que el display responda de verdad, preguntándoselo a él, en vez
#    de dormir a ciegas: si el navegador arranca antes que X, muere sin ventana.
i=0
while ! xdpyinfo -display :99 >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 100 ] && { echo "Xvfb no arrancó en 10 s"; exit 1; }
  sleep 0.1
done

# 3. Gestor de ventanas. Sin él, X deja el foco «siguiendo al puntero» y los
#    diálogos y desplegables de Chrome se comportan de forma errática — justo
#    lo que tiene que ir fino para teclear la contraseña y, si aparece,
#    arrastrar el deslizador de DataDome. openbox pesa poco y basta.
openbox &

# 3b. Y esperar a que esté GOBERNANDO de verdad, no solo lanzado: si el
#     navegador abre su ventana antes de que el gestor tome el display, la
#     ventana puede quedarse sin pintar —negra— indefinidamente.
i=0
while ! xprop -root _NET_SUPPORTING_WM_CHECK >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 100 ] && { echo "el gestor de ventanas no arrancó en 10 s"; break; }
  sleep 0.1
done

# 4. VNC solo por localhost, y noVNC (websockify) como puente al navegador.
x11vnc -display :99 -localhost -nopw -forever -shared -quiet &
websockify --web /usr/share/novnc 6080 localhost:5900 &

# 5. El trabajo, como proceso principal: al terminar, muere el contenedor.
#    Correr con --init para que no queden zombis.
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
exec python -m scripts.login_reuters
