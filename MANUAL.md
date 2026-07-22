# Manual de newsphotostalker

Guía de uso completa. Para una visión rápida, mira el [README](README.md).

## Índice
- [Instalación](#instalación)
- [Primer arranque](#primer-arranque)
- [El panel](#el-panel)
- [Crear y editar búsquedas](#crear-y-editar-búsquedas)
- [Tipos de búsqueda por agencia](#tipos-de-búsqueda-por-agencia)
- [Refresco: automático y manual](#refresco-automático-y-manual)
- [Rellenar histórico](#rellenar-histórico)
- [Retención y purga](#retención-y-purga)
- [Reuters: el login](#reuters-el-login)
- [Modo mock vs live](#modo-mock-vs-live)
- [Usuarios](#usuarios)
- [Avisos por webhook](#avisos-por-webhook)
- [Arquitectura](#arquitectura)
- [Problemas frecuentes](#problemas-frecuentes)

## Instalación

Requisitos: **Python 3.10+** y, para el modo real de Reuters, **Google Chrome**.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.yaml config.local.yaml
```

`config.local.yaml` nunca se sube (está en `.gitignore`). Arranca en modo `mock`,
así que funciona sin credenciales.

## Primer arranque

```bash
python -m scripts.seed        # siembra las 3 búsquedas de ejemplo
uvicorn app.main:app          # http://127.0.0.1:8000
```

En Windows puedes usar **`arrancar_servidor.bat`** (arranca el servidor, abre el
navegador y deja la ventana abierta; cerrarla detiene el servidor).

Usuario inicial: **`admin` / `admin`**. Cámbialo en *ajustes → usuarios*.

## El panel

- **Portada**: la lista de tus búsquedas, con su estado (● verde = OK, rojo =
  error, gris = pausada), la agencia y acciones (↻ ejecutar ahora, ✎ editar).
- **Vista de búsqueda**: la galería paginada de esa búsqueda, con pie de foto,
  autor, crédito y fecha. Al final de la última página está el botón **⤓ Rellenar
  histórico**.
- **ajustes**: refresco global, fotos por página, usuarios y credenciales.

## Crear y editar búsquedas

Botón **+ Nueva búsqueda**. Campos:

- **Nombre** (opcional): etiqueta libre; si lo dejas vacío se genera solo.
- **Agencia**: AP, Reuters, AFP (vía Getty) o Getty.
- **Tipo**: *Fotógrafo* o *Búsqueda de texto*.
- **Consulta**: el nombre del fotógrafo o los términos.
- **Retención**: por tiempo (meses) o por espacio (MB).
- **Activa**: si se refresca junto a las demás.

## Tipos de búsqueda por agencia

| Agencia | Fotógrafo | Texto |
|---|---|---|
| AP | `photographer.name:"…"` (exacto) | términos libres (p. ej. `APTOPIX`) |
| Getty / AFP | **nombre de artista EXACTO y completo** | frase libre |
| Reuters | nombre del fotógrafo (busca sus créditos) | frase libre |

> **Getty y los nombres**: `artistexact` exige el nombre **completo y exacto** del
> artista. Un nombre parcial puede no coincidir o caer en un homónimo antiguo
> (p. ej. usa *"Pablo Blázquez Domínguez"*, no *"Pablo Blázquez"*). Los **acentos**
> sí los resuelve solo (reintenta sin acentos si hace falta).

## Refresco: automático y manual

El refresco es **global**: todas las búsquedas activas se ejecutan **juntas**. En
*ajustes* configuras **cada cuánto** (X horas o X días) y **a partir de qué hora**.

- **Ejecutar una ahora**: botón ↻ en la portada o en la vista de la búsqueda.
- **Actualizar todas ahora**: botón en *ajustes*.

Cada ejecución solo baja las **novedades** (lo más nuevo desde la última vez), así
que es rápida. El primer llenado de una búsqueda se hace con «rellenar histórico».

## Rellenar histórico

La retención dice cuánto se **conserva**, no cuánto se **descarga**. Para llenar una
búsqueda hacia atrás (hasta su límite de retención), entra en ella, ve al final de
la última página y pulsa **⤓ Rellenar histórico**. Corre en segundo plano (lo verás
en *actividad*) y puede tardar varios minutos en fotógrafos prolíficos.

## Retención y purga

Cada búsqueda define una política que se aplica al final de cada ejecución:

- **Por tiempo**: conserva solo las fotos de los últimos *N* meses.
- **Por espacio**: mantiene el total por debajo de *N* MB.

La purga borra los ficheros en disco y sus registros.

## Reuters: el login

Reuters Connect exige sesión y está tras **DataDome**, así que se usa un navegador
real con tu login, que haces **a mano una vez**:

```bash
python -m scripts.login_reuters      # Windows: login_reuters.bat
```

Se abre Chrome en la página de login; entra (email, contraseña y el deslizador si
aparece). La sesión queda en el perfil (`data/browser/`) y se reutiliza. Si caduca,
repite el login. Consejos:

- Apunta `playwright.executable_path` a tu **Google Chrome** (mejor huella que el
  Chromium de Playwright, que además en Windows falla en modo headed).
- En un **servidor sin pantalla**, arranca bajo Xvfb y haz el login por VNC/noVNC
  (ver [`deploy/`](deploy/)).

## Modo mock vs live

`mode` en `config.local.yaml`:

- **mock**: fotos sintéticas realistas. Prueba todo sin tocar los servicios reales.
- **live**: adaptadores reales (necesita credenciales de Reuters para esa agencia).

## Usuarios

El **admin** crea/edita/borra usuarios desde *ajustes*. Los demás usuarios hacen lo
mismo salvo gestionar usuarios, y **cada uno tiene sus propias búsquedas y fotos**.
Borrar un usuario elimina sus búsquedas y fotos. Contraseñas con PBKDF2; sesión por
cookie firmada (con opción «recordar»).

## Avisos por webhook

Opcional. Si defines `alerts.webhook_url`, se hace un POST JSON `{subject, message}`
la **primera** vez que una agencia falla tras funcionar (disparo por flanco; se
rearma al recuperarse). Puedes apuntarlo a cualquier flujo que reenvíe por email.

## Arquitectura

```
app/
  config.py     Configuración + credenciales (YAML + env)
  database.py   Engine/sesión SQLAlchemy (SQLite, WAL)
  models.py     User, Search, Asset, RunLog, AppSettings
  storage.py    Ficheros en disco
  retention.py  Purga por tiempo / espacio
  scheduler.py  Refresco global (APScheduler) + ejecución manual + backfill
  services.py   Lógica del panel (CRUD, stats, actividad, ajustes)
  alerts.py     Aviso por flanco vía webhook
  main.py       App FastAPI (panel + actividad + ajustes + API JSON)
  ingest/
    base.py, http_base.py, live_base.py   Bases de adaptador
    ap.py, getty.py, reuters.py, mock.py  Adaptadores
    runner.py     Orquesta una ejecución / backfill
    keepalive.py  Mantiene viva la sesión de Reuters (opcional)
scripts/  seed.py · run_once.py · login_reuters.py
tests/    batería de pruebas
```

API JSON (con sesión): `GET /api/status`, `GET /api/searches`.

## Problemas frecuentes

- **Reuters da error / 0 fotos**: la sesión caducó → repite `login_reuters`.
- **Getty devuelve 0**: revisa que el nombre de artista sea el **exacto y completo**.
- **Chromium no arranca con el perfil en una unidad de red**: pon
  `playwright.user_data_dir` en una ruta **local**.
- **Una búsqueda se queda en pocas fotos**: usa **⤓ Rellenar histórico** (la
  retención limita cuántas se conservan).
