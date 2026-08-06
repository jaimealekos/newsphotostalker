# Despliegue

Tres formas de correr newsphotostalker.

## 1. Windows, a demanda

Doble clic en `arrancar_servidor.bat` (en la raíz). Arranca el servidor, abre el
navegador y deja la ventana abierta. Es la forma más simple si solo consultas de
vez en cuando; además el login de Reuters se hace en una ventana de Chrome normal.

## 2. Docker (servidor 24/7)

Desde la raíz del repo:

```bash
cp config.example.yaml config.local.yaml    # mode: live, credenciales,
                                             # playwright.executable_path: /usr/bin/google-chrome
docker compose up -d --build                 # panel en http://SERVIDOR:8000
```

La imagen incluye **Google Chrome** y lanza el navegador bajo **Xvfb** (Reuters
corre headed). `data/` y `config.local.yaml` se montan como volúmenes.

### Login de Reuters en un servidor sin pantalla

El login de Reuters necesita interacción humana una vez. En un servidor sin
pantalla, usa la imagen **noVNC**: levanta un escritorio virtual con el
navegador dentro y lo publica en tu navegador de siempre.

```bash
docker build -t newsphotostalker-vnc -f deploy/docker/Dockerfile.vnc deploy/docker
docker run --rm -it --init --shm-size=1g -p 6080:6080 \
  -v "$PWD/data":/srv/app/data \
  -v "$PWD/config.local.yaml":/srv/app/config.local.yaml:ro \
  newsphotostalker-vnc
```

El login va en **dos pasos**, y la terminal es parte del proceso:

1. Arranca el contenedor con la orden de arriba. **Deja esa terminal abierta**:
   se queda esperando, y eso es lo correcto.
2. Abre `http://SERVIDOR:6080/vnc.html` y entra en Reuters en la ventana que
   verás ahí —correo, contraseña y el deslizador de DataDome si aparece—
   **hasta ver tu panel**, no a medias.
3. Vuelve a la terminal y pulsa **Intro**. Ahí se cierra el navegador y se
   comprueba de verdad si la sesión quedó; te lo dice, no lo da por hecho.

La sesión queda en `data/browser/` (volumen compartido) y la app la reutiliza.
Al terminar, el contenedor se para solo.

Verás un aviso de Chrome sobre `--no-sandbox` y errores de `dbus` en la
consola: son normales dentro de un contenedor y no afectan al login.

Las dos opciones raras del `docker run` no son adorno:

- **`-it`** — el Intro del paso 3 tiene que llegar a algún sitio. Sin esto la
  entrada está cerrada, el programa lee fin-de-fichero, da el Intro por pulsado
  al instante y termina con «no se detectó la sesión» antes de que te dé tiempo
  a abrir el navegador.
- **`--shm-size=1g`** — Docker da 64 MB a `/dev/shm` y el renderizador de Chrome
  se ahoga ahí: la ventana abre, la pestaña dice «Login | Reuters Connect»… y la
  página es un «Aw, Snap!» (SIGTRAP). Las búsquedas no lo sufren porque
  Playwright pasa `--disable-dev-shm-usage` por su cuenta; el login abre el
  navegador del sistema a pelo y no.

### Si la pantalla de noVNC sale negra

Con el contenedor en marcha, desde otra terminal:

```bash
docker exec <contenedor> sh -c 'DISPLAY=:99 xwininfo -root -children'
```

Si no aparece ninguna ventana grande, el navegador no llegó a arrancar. Si
aparece, arrancó y el problema está en lo que pinta — y entonces lo más probable
es esto:

> **Chrome 151 falla al pintar bajo Xvfb.** Medido A/B en el mismo servidor, con
> el mismo flujo y las mismas opciones, cambiando solo el navegador: con Chrome
> **150.0.7871.181** el login se ve; con **151.0.7922.75** la ventana existe pero
> se queda negra. Chrome intenta Vulkan por ZINK, falla
> (`VK_ERROR_INCOMPATIBLE_DRIVER`) y no llega a componer nada. La imagen ya
> arranca con `--disable-features=Vulkan`, que lo evita casi siempre, pero **no
> del todo**: en pruebas repetidas hubo tandas enteras en negro y, minutos
> después, la misma imagen funcionando. No está resuelto de raíz.
>
> Si te toca: para el contenedor y vuelve a lanzarlo, que suele bastar. Si
> insiste, usa la alternativa de aquí abajo, que no depende de nada de esto.
>
> Las **búsquedas no sufren este problema**: las lanza Playwright con sus propias
> opciones (verificado con Chrome 151). Es exclusivo del login, que abre el
> navegador del sistema tal cual.

**Alternativa sin noVNC:** haz el login en tu portátil, exporta la sesión
(*ajustes → exportar sesión*) e impórtala en el servidor (*ajustes → importar
sesión*). Viaja un JSON con las cookies ya descifradas, así que cruza entre
sistemas. No te ahorra el login, lo mueve a donde hay pantalla.

## 3. systemd + nginx (sin Docker)

Plantillas en este directorio:

- `newsphotostalker.service` — servicio systemd (el motor escucha en un socket
  unix; ajusta usuario y rutas).
- `nginx.conf.example` — nginx como único expuesto: sirve `/static` y `/media`
  desde disco y pasa lo dinámico al socket.

Reuters headed necesita display: arranca bajo `xvfb-run` (ver comentario en el
`.service`) y haz el primer login con `python -m scripts.login_reuters`.

## Fichero `docker/with-xvfb.sh`

Arranca Xvfb de forma robusta (limpia locks de X huérfanos que, tras un
`docker restart`, impedirían que Xvfb rearranque) y ejecuta el comando principal.
