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
pantalla, usa la imagen **noVNC** y hazlo desde el navegador de tu equipo:

```bash
docker build -t newsphotostalker-vnc -f deploy/docker/Dockerfile.vnc deploy/docker
docker run --rm --init -p 6080:6080 -v "$PWD/data":/srv/app/data newsphotostalker-vnc
# abre http://SERVIDOR:6080/vnc.html y haz el login; al terminar, para el contenedor
```

La sesión queda en `data/browser/` (volumen compartido) y la app la reutiliza.

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
