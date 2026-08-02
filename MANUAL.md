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
- [Resolución de las fotos](#resolución-de-las-fotos)
- [Tu cuenta](#tu-cuenta)
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

Usuario inicial: **`admin` / `admin`**. Cámbialo en *ajustes → tu cuenta*.

## El panel

- **Portada**: la lista de tus búsquedas, con su luz de novedades (● verde =
  han entrado fotos desde la última vez que abriste **esa** búsqueda, gris = sin
  novedades; ⏸ = búsqueda pausada), la fecha de la última novedad, la agencia y
  las acciones (↻ ejecutar ahora, ✎ editar).
- **Vista de búsqueda**: la galería paginada de esa búsqueda, con pie de foto,
  autor, crédito y fecha. Al final de la última página está el botón **⤓ Rellenar
  histórico**.
- **ajustes**: refresco global, fotos por página, tu cuenta y credenciales.

### La luz de novedades

Cada búsqueda lleva su propia marca de «visto»: la luz se enciende cuando entra
una foto después de la última vez que **abriste esa búsqueda**, y se apaga solo
al abrirla. Mirar la portada no apaga nada, y entrar en una búsqueda no apaga las
demás. La marca vive en la base de datos, no en una cookie, así que sobrevive a
cambiar de navegador o a borrar los datos de navegación.

La columna **última novedad** muestra la fecha de la foto más reciente, y solo
aparece en las búsquedas que tienen la luz encendida. Pasando el ratón por encima
se ve cuándo entró en el panel (que puede ser bastante después de hacerse).

### Ordenar el panel y agrupar con separadores

El botón **⇅ Ordenar** de la portada entra en el modo edición:

- **arrastra** cualquier fila (por el asa ⠿ o por la fila entera) para colocarla;
- **+ Separador** añade una línea de título entre búsquedas; escribe su nombre en
  el propio campo y **✕** lo quita;
- todo se guarda solo al soltar o al escribir — el aviso de la cabecera lo
  confirma («Orden guardado ✓»);
- **✓ Hecho** vuelve al panel normal.

El orden y los separadores se guardan en la cuenta, no en el navegador. Mientras
ordenas, el panel no se autorrefresca (si no, perderías el arrastre a medias).

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
repite el login.

**Ese es el único momento en que verás una ventana.** Las ejecuciones normales van
en headless: no se abre nada. Verificado en agosto de 2026 que DataDome deja pasar
el headless nuevo de Chrome cuando la sesión ya está guardada en el perfil — lo que
bloqueaba era el headless del Chromium que empaqueta Playwright. Se controla con
`playwright.headless` (por defecto `true`); `login_reuters` abre la ventana pase lo
que pase, porque ahí hace falta una persona.

Consejos:

- Deja `playwright.executable_path` en `null`: en Windows la app coge sola el
  **Google Chrome** instalado, que además da mejor huella que el Chromium de
  Playwright (y ese, en Windows, ni arranca headed).
- En un **servidor sin pantalla** ya no hace falta Xvfb para las ejecuciones
  normales; solo para el login manual, por VNC/noVNC (ver [`deploy/`](deploy/)).

## Modo mock vs live

`mode` en `config.local.yaml`:

- **mock**: fotos sintéticas realistas. Prueba todo sin tocar los servicios reales.
- **live**: adaptadores reales (necesita credenciales de Reuters para esa agencia).

## Resolución de las fotos

Lo que se guarda es la **preview** que cada agencia deja descargar sin licencia, y
no todas dan lo mismo:

| Agencia | Preview guardada | Miniatura | Tope del servicio |
|---|---|---|---|
| **Getty / AFP** | 2048 px (con marca de agua) | 612 px | 2048 |
| **AP** | 1024 px (con marca de agua) | la misma | 1024 sin login |
| **Reuters** | 640 px | la misma | 800 px, pero sale caro (ver abajo) |

Detalle de Getty: la URL del listado es de 612 px y su firma va **atada a ese
tamaño** (pedir `s=2048x2048` sobre ella devuelve 400). El comp grande va firmado
aparte y solo aparece en la ficha de la foto, así que cada foto **nueva** cuesta
una petición extra; si esa petición falla, se guarda la de 612 de siempre en vez
de perder la foto.

Por qué AP y Reuters no suben: la rendición `main` de AP (la de 5000 px) responde
403 sin licencia, y la URL de Reuters va firmada **incluyendo el tamaño**, así que
pedirle 1024 o 2048 da 403. Reuters sí publica 800 px, pero solo en la ficha del
ítem, que hay que abrir con el navegador: unos 10 s por foto para pasar de 640 a
800. No compensa, así que se queda en 640.

Ojo con la **retención por espacio**: una foto de Getty pasa de ~50 KB a ~1 MB, así
que un límite en MB que antes daba para miles de fotos ahora da para muchas menos.

En la ficha, todas las fotos se ven al mismo ancho (1024) para que unas no salgan
de golpe más pequeñas que otras; **un clic** las lleva a su resolución real. Las de
Getty descargadas antes de la 1.1 siguen siendo de 612 px y ahí van ampliadas — el
pie de la imagen lo avisa.

## Tu cuenta

newsphotostalker es de **un solo usuario**. Desde *ajustes → tu cuenta* cambias el
nombre y la contraseña. Contraseñas con PBKDF2; sesión por cookie firmada (con
opción «recordar»).

## Avisos por webhook

Opcional. Si defines `alerts.webhook_url`, se hace un POST JSON `{subject, message}`
la **primera** vez que una agencia falla tras funcionar (disparo por flanco; se
rearma al recuperarse). Puedes apuntarlo a cualquier flujo que reenvíe por email.

## Arquitectura

```
app/
  config.py     Configuración + credenciales (YAML + env)
  database.py   Engine/sesión SQLAlchemy (SQLite, WAL)
  models.py     User, Search, Separator, Asset, RunLog, AppSettings
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
- **`spawn UNKNOWN` al lanzar una búsqueda de Reuters (Windows)**: el Chromium
  que empaqueta Playwright no arranca en modo headed en bastantes máquinas
  Windows (falla por SxS y Playwright lo enmascara con ese mensaje). Desde la
  1.1 la app usa **automáticamente el Google Chrome instalado** si no has puesto
  `playwright.executable_path`, así que basta con tener Chrome. Si no lo tienes,
  el error del panel te lo dice.
- **Chromium no arranca con el perfil en una unidad de red**: pon
  `playwright.user_data_dir` en una ruta **local**.
- **Una búsqueda se queda en pocas fotos**: usa **⤓ Rellenar histórico** (la
  retención limita cuántas se conservan).
