# Manual de newsphotostalker

Guía de uso completa. Para una visión rápida, mira el [README](README.md).

## Índice
- [Instalación](#instalación)
- [Primer arranque](#primer-arranque)
- [El panel](#el-panel)
- [Grupos y el feed de un grupo](#grupos-ordenar-el-panel-y-ver-un-grupo-entero-de-corrido)
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

### La versión descargable de Windows

Descomprime el `.zip` de la [release](../../releases) y haz doble clic en
`newsphotostalker.bat`. No hay instalador ni hace falta tener Python: el paquete
trae dentro su propia copia de Python. No se abre ninguna ventana: el programa
vive en el **icono junto al reloj**, desde donde se abre el panel y se sale. En
la carpeta está todo a la vista: `python\`, `app\`, `run.py`.

¿Algo falla y quieres ver qué pasa por dentro? `newsphotostalker (consola).bat`
arranca igual pero con ventana, enseñando el detalle; y el registro queda
siempre en `data/newsphotostalker.log`, se use el lanzador que se use.

Junto al `.bat` aparecen, la primera vez:

| | |
|---|---|
| `config.local.yaml` | ajustes editables (modo, refresco, avisos) |
| `data/app.db` | la base de datos: búsquedas, fotos indexadas, tu cuenta |
| `data/media/` | las fotos descargadas, por agencia y búsqueda |
| `data/browser/` | la sesión de Reuters |

Todo lo tuyo vive ahí: para hacer copia de seguridad o mudarte de ordenador,
copia esa carpeta. No lo descomprimas dentro de `C:\Archivos de programa`, que
Windows no deja escribir ahí.

No hay que instalar nada más: Python y Playwright viajan dentro. Para Reuters se
usa un navegador del sistema — el **Google Chrome** que tengas o, si no lo tienes,
el **Microsoft Edge que ya trae Windows** — y hay que entrar una vez desde
*ajustes → iniciar sesión en Reuters* (ver [Reuters: el login](#reuters-el-login)).
En esa misma pantalla se ve cuál va a usar.

### Desde el código

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

### Grupos: ordenar el panel y ver un grupo entero de corrido

Cada búsqueda vive dentro de un **grupo**. Ninguna se queda fuera: la que no
tenga uno propio aparece en **«Sin grupo»**, que es también donde nacen las
recién creadas y donde vuelven las que se quedan sin casa.

El botón **Grupos y orden** de la portada entra en el modo edición:

- **arrastra** cualquier fila (por el asa ⠿ o por la fila entera) para colocarla,
  y **suéltala en otro grupo para moverla ahí** — como se mueve una canción entre
  listas de reproducción. Una búsqueda pertenece al grupo cuyo rótulo tiene
  encima, así que no hay nada más que tocar;
- **arrastrar el rótulo de un grupo mueve el grupo entero**, con sus búsquedas;
- **+ Grupo** crea uno nuevo; escribe su nombre en el propio campo;
- **✕** quita el grupo. **Sus búsquedas no se borran**: pasan a «Sin grupo». Para
  borrar una búsqueda de verdad está su propio botón, dentro de ✎ Editar;
- todo se guarda solo al soltar o al escribir — el aviso de la cabecera lo
  confirma («Guardado ✓»);
- **✓ Hecho** vuelve al panel normal.

También puedes cambiar una búsqueda de grupo desde **✎ Editar**, en el
desplegable *Grupo*.

Los grupos y el orden se guardan en la cuenta, no en el navegador. Mientras
ordenas, el panel no se autorrefresca (si no, perderías el arrastre a medias).

#### El feed de un grupo

En el panel normal, **pulsa el nombre de un grupo** y verás **todas las fotos de
todas sus búsquedas mezcladas, de la más nueva a la más vieja**. Es la forma de
leer un bloque entero —«los de agencia en Madrid», «la guerra»— sin ir entrando
búsqueda por búsqueda.

Arriba se listan las búsquedas de las que sale el feed, y cada una lleva a la
suya. Abrir el feed **no apaga ninguna luz** de novedades: es para leer, y dar
por vistas quince búsquedas de golpe borraría justo lo que el panel usa para
decirte dónde ha entrado algo. Las fotos que no habías visto salen destacadas
igual que dentro de su búsqueda.

> **¿Vienes de la versión 1.2?** Tus separadores se convierten en grupos solos, y
> el panel queda exactamente como estaba: cada separador pasa a ser el grupo de
> las búsquedas que tenía debajo, y lo que iba antes del primero se queda en «Sin
> grupo». Los separadores sin rótulo pasan a llamarse «Sin título» — un grupo hay
> que poder pulsarlo, y una etiqueta en blanco no se puede pulsar; renómbralo
> desde *Grupos y orden*.

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
real con tu login, que haces **a mano una vez**. Desde el panel:

> *ajustes → sesión de Reuters → **iniciar sesión en Reuters***

O desde la línea de órdenes, si trabajas con el código:

```bash
python -m scripts.login_reuters      # Windows: login_reuters.bat
```

**El programa no teclea tu contraseña jamás, ni hay que ponerla en ningún
fichero.** Reuters contabiliza los intentos de login automatizados como intentos
fallidos y acaba bloqueando la cuenta (pasó, y por eso se eliminó): el login lo
haces siempre tú, a mano. El campo `password` de la configuración no se usa;
`username` es solo un recordatorio de qué cuenta es.

Se abre tu navegador (Chrome o Edge) en la página de login. Entra —email,
contraseña y el CAPTCHA de DataDome si aparece— hasta ver tu panel de Reuters, y
**cierra la ventana: la sesión se comprueba y se guarda sola**. Queda en el
perfil (`data/browser/reuters`) y se reutiliza.

A partir de ahí, la app **mantiene la sesión viva ella sola**: cada hora hace una
búsqueda mínima que renueva el token (`reuters_keepalive_minutes`, 60 por
defecto; 0 la apaga). Aun así la sesión tiene fecha de caducidad —semanas; el
programa mide cuánto aguanta de verdad— y cuando muera lo dirá el panel (y el
aviso por webhook, si lo tienes): repites el login y listo, un minuto.

**Se usa tu navegador tal cual, sin automatizar, a propósito.** DataDome ficha la
huella de un navegador conducido por software y planta un CAPTCHA que se atasca; un
navegador humano normal, sobre un perfil aparte de la app, lo pasa. Ese es el único
momento en que verás una ventana: las búsquedas van en headless y no abren nada.

### Si DataDome te bloquea («acceso restringido temporalmente»)

DataDome también mira la **reputación de la IP**. Si desde tu red se ha golpeado
mucho a Reuters de forma automatizada, puede bloquear un rato aunque el navegador
sea normal. No es un fallo del programa:

- **Espera** — el bloqueo es temporal (de minutos a unas horas) y se levanta solo.
- Reintenta el login cuando se calme.
- Si tienes prisa, entra desde **otra red** (p. ej. compartiendo datos del móvil):
  inicia sesión ahí y trae la sesión con *exportar sesión* / *importar sesión*.

Consejos:

- Deja `playwright.executable_path` en `null`: en Windows la app coge sola el
  **Google Chrome** o el **Edge** instalado.
- En un **servidor sin pantalla** no hay dónde abrir la ventana; usa el traspaso
  de sesión desde un equipo con pantalla (*exportar* / *importar*).

## Modo mock vs live

`mode` en `config.local.yaml`:

- **mock**: fotos sintéticas realistas. Prueba todo sin tocar los servicios reales.
- **live**: adaptadores reales (para Reuters hace falta haber iniciado sesión a
  mano una vez; el resto no pide nada).

## Resolución de las fotos

Lo que se guarda es la **preview** que cada agencia deja descargar sin licencia, y
no todas dan lo mismo:

Cada agencia baja **lo máximo que deja descargar**, y son cifras muy distintas:

| Agencia | Preview guardada | Miniatura | Es su tope porque… |
|---|---|---|---|
| **Getty / AFP** | 2048 px (con marca de agua) | 612 px | es el mayor comp que publica |
| **AP** | 1024 px (con marca de agua) | la misma | la rendición `main` da 403 sin licencia |
| **Reuters** | 640 px | la misma | no publica ninguna mayor accesible |

Detalle de Getty: la URL del listado es de 612 px y su firma va **atada a ese
tamaño** (pedir `s=2048x2048` sobre ella devuelve 400). El comp grande va firmado
aparte y solo aparece en la ficha de la foto, así que cada foto **nueva** cuesta
una petición extra; si esa petición falla, se guarda la de 612 de siempre en vez
de perder la foto.

Por qué AP y Reuters no suben más, comprobado contra los servicios: la rendición
`main` de AP (la de 5000 px) responde 403 sin licencia. Y la URL de Reuters lleva
el tamaño en la propia ruta y va firmada **incluyendo ese tramo**, así que pedirle
1024 o 2048 da 403; su ficha publica una ruta `/watermark/…/800x800`, pero va sin
firmar y responde 403 hasta desde dentro de la propia página con la sesión abierta
— la ficha misma no llega a mostrar más de 640.

Ojo con la **retención por espacio**: una foto de Getty pasa de ~50 KB a ~1 MB, así
que un límite en MB que antes daba para miles de fotos ahora da para muchas menos.

En la ficha, la foto se ve **a su resolución real y nunca ampliada**: si no cabe a
lo ancho se reduce, y un clic la devuelve a tamaño nativo. Por eso una de Getty
recién bajada (2048) se ve mayor que una de Reuters (640): es su tamaño de verdad,
no un estirón.

## Tu cuenta

newsphotostalker es de **un solo usuario**. Desde *ajustes → tu cuenta* cambias el
nombre y la contraseña. Contraseñas con PBKDF2; sesión por cookie firmada (con
opción «recordar»).

## Avisos por webhook

Opcional. Si defines `alerts.webhook_url`, se hace un POST JSON `{subject, message}`
cuando algo se rompe, con disparo **por flanco**: un aviso la primera vez que algo
falla tras funcionar, silencio mientras siga igual, y rearme al recuperarse.
Puedes apuntarlo a cualquier flujo que reenvíe por email. Avisan tres cosas:

- **«_agencia_ ha dejado de funcionar»** — el veredicto del refresco global
  entero: la agencia se da por buena solo si TODAS sus búsquedas fueron bien
  (el éxito de una búsqueda suelta no rearma el disparador). Se filtra con
  `alerts.agencies`; por defecto, solo Reuters.
- **«la sesión de Reuters ha caducado»** — hace falta repetir el login manual, y
  el aviso dice cuántos días aguantó la sesión. No pasa por el filtro
  `alerts.agencies` a propósito: no cuenta una avería del servicio, sino que
  hace falta una persona. Una sesión muerta del todo puede producir este correo
  Y el de la agencia, cada uno una sola vez.
- **El keep-alive no da señales de vida** — el programa ha dejado de ejercitar
  la sesión (planificador caído, navegador que no arranca). Tampoco pasa por el
  filtro.

¿No tienes ningún webhook a mano? [ntfy.sh](https://ntfy.sh) funciona sin crear
cuenta: los avisos llegan a un tema que ves en el navegador o, como
notificación, en su app del móvil:

```yaml
alerts:
  enabled: true
  webhook_url: "https://ntfy.sh/UN-TEMA-SECRETO?tpl=yes&t={{.subject}}&m={{.message}}"
```

Elige un tema largo e impredecible (hace de contraseña). Para que además te lo
reenvíe **por correo**, ntfy pide una cuenta gratuita: con su token, añade a la
URL `&email=tu@correo.com&auth=<token en base64>`. Y `alerts.postdata`, si lo
defines, se añade al final de todos los avisos: instrucciones para quien lo
reciba.

## Arquitectura

```
app/
  config.py     Configuración + credenciales (YAML + env)
  database.py   Engine/sesión SQLAlchemy (SQLite, WAL)
  models.py     User, Search, SearchGroup, Asset, RunLog, AppSettings
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
    keepalive.py  Mantiene viva la sesión de Reuters (búsqueda mínima cada hora)
scripts/  seed.py · run_once.py · login_reuters.py
tests/    batería de pruebas
```

API JSON (con sesión): `GET /api/status`, `GET /api/searches`.

## Problemas frecuentes

- **Reuters da error**: el mensaje ya dice cuál de las tres averías es. «La
  sesión ha caducado» → repite `login_reuters`. «Muro/challenge de DataDome» →
  transitorio: espera a que se levante (ver la sección de DataDome); re-loguear
  contra el muro no ayuda. «La página carga con la sesión viva, pero no pinta ni
  una tarjeta» → **es un corte de Reuters, no tuyo**: no toques la sesión, se
  recupera solo. Visto dos veces (27-08-2026 unas 4 h y 31-08-2026 unas 8 h), y
  las dos con la misma señal: falla en **todas** las búsquedas a la vez y el
  keep-alive sigue viendo la sesión viva. El diagnóstico que acompaña al mensaje
  —título, URL, nodos de la maqueta y un trozo del texto— es lo que permite
  distinguirlo de un cambio de maqueta si algún día deja de encajar.
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
