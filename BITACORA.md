# Bitácora de newsphotostalker

> **Fichero de reenganche.** Si acabas de llegar —una persona nueva o una sesión
> nueva de Claude Code— lee esto entero antes de tocar nada. En cinco minutos
> sabes qué es el programa, cómo está montado, qué tiene de delicado y qué no se
> debe hacer.
>
> Los cambios, uno a uno y por fechas, van en [REGISTRO.md](REGISTRO.md).
> Cómo se usa el programa, en [MANUAL.md](MANUAL.md).
>
> **Última actualización: 2026-09-02.**

---

## 1. Qué es esto

Una herramienta para **vigilar el trabajo de los fotógrafos de agencia**
(AP, Reuters, AFP y Getty) desde una sola pantalla, en vez de ir web por web.

Le das el nombre de un fotógrafo o una búsqueda de texto, y el programa busca
cada cierto tiempo, se descarga lo nuevo y lo guarda en tu disco con su pie de
foto, autor, crédito y fecha. En el panel, una luz roja por búsqueda te dice
dónde han entrado fotos.

Es un programa **de escritorio, de un solo usuario**, gratis y con licencia MIT.
No tiene servidor propio, ni cuentas, ni telemetría: habla con las cuatro
agencias y con nadie más.

## 2. Cómo está montado

Python + **FastAPI** (el panel es una web que se abre en tu navegador), **SQLite**
para los datos y **Playwright** (un navegador automatizado) **solo para Reuters**.
El programa vive en el icono junto al reloj; no hay ventana de consola.

Las piezas que hay que conocer:

| Pieza | Qué hace |
|---|---|
| `app/main.py` | La web: panel, actividad, ajustes, API JSON. |
| `app/models.py` | Los datos: usuario, búsquedas, separadores, fotos, historial. |
| `app/scheduler.py` | El reloj: lanza el **refresco global** (todas las búsquedas juntas). |
| `app/ingest/runner.py` | Ejecuta **una** búsqueda de principio a fin: busca, descarga, guarda, purga. |
| `app/ingest/factory.py` | Elige el adaptador según la agencia y el modo. |
| `app/ingest/ap.py`, `getty.py`, `reuters.py` | Un adaptador por agencia (Getty sirve también a AFP). |
| `app/ingest/mock.py` | Fotos falsas realistas, para probarlo todo sin tocar las agencias. |
| `app/ingest/keepalive.py` | Mantiene viva la sesión de Reuters y vigila que él mismo siga corriendo. |
| `app/alerts.py` | Los avisos por webhook cuando algo deja de funcionar. |
| `app/retention.py` | Borra lo viejo: por meses o por megas. |
| `app/bandeja.py` | El icono junto al reloj. |

Hay dos **modos**, en `config.local.yaml`:

- **mock** — fotos sintéticas. Sirve para probar el sistema entero sin salir a la red.
- **live** — adaptadores de verdad. Es el modo del despliegue real.

> ⚠️ El `config.local.yaml` de esta carpeta está en **live** con la cuenta real de
> Reuters. Arrancar el servidor aquí sale a buscar de verdad contra las agencias.
> Para comprobar que el entorno va, con los tests basta.

## 3. Las cuatro agencias

Tres no necesitan cuenta; solo Reuters.

| Agencia | Cuenta | Cómo se baja | Tamaño que se guarda |
|---|---|---|---|
| **AP** | no | API de búsqueda anónima | 1024 px |
| **Getty** | no | páginas de búsqueda por HTTP | 2048 px |
| **AFP** | no | igual, va distribuida por Getty | 2048 px |
| **Reuters** | **la tuya** | navegador con tu sesión | 640 px |

Lo que se guarda es la **preview con marca de agua** que cada agencia deja bajar
sin licencia; los tamaños son el tope de cada una, comprobado contra el servicio.
El programa encuentra y vigila el trabajo: licenciar es cosa tuya y de la agencia.

Rarezas que muerden: Getty exige el **nombre de artista exacto y completo** (los
acentos sí los resuelve solo), y AP distingue entre el término de búsqueda
(`query`) y el tipo de búsqueda (`st`).

## 4. Reuters, que es el tema delicado

Casi todo el trabajo reciente ha ido aquí, así que conviene entenderlo.

**Reuters Connect está detrás de DataDome**, un muro anti-bot. Eso obliga a tres
decisiones que parecen raras y no lo son:

1. **El login lo hace siempre una persona, a mano, una vez.** Se abre el Chrome
   normal del sistema (no el de Playwright: DataDome reconoce un navegador
   automatizado), resuelves el acceso y al cerrar la ventana la sesión se
   comprueba y se guarda en el perfil.
2. **El programa no teclea nunca la contraseña.** En agosto de 2026 los intentos
   de re-login automático hicieron que **Reuters bloqueara la cuenta por IP**. No
   se vuelve a intentar: cada intento fallido contra el muro cuenta.
3. **El keep-alive solo MANTIENE viva una sesión ya abierta**, no la rehace. Cada
   hora hace una búsqueda mínima de verdad, que es lo que renueva el token. Si
   encuentra la sesión caída, avisa a un humano y ya está.

Cuánto dura una sesión no es público y se mide en vivo: cada aviso lleva puesto
«la sesión aguantó *N* días». Lo medido hasta hoy (datos del NAS, 02-09-2026):
la primera sesión tras el desbaneo murió en 1.0 días —de ahí salió la sospecha
de un tope duro de ~24 h—, pero la siguiente lleva **más de 13 días viva** con el
keep-alive horario, así que esa sospecha queda **descartada**: bien ejercitada,
la sesión es de larga vida, y aquella caída temprana fue otra cosa (probablemente
la resaca del desbaneo). El re-login manual es excepcional, no diario.

**Y hay una avería que NO es la sesión: los cortes de Reuters.** Dos veces
medidas (27-08-2026 unas 4 h; 31-08-2026 unas 8 h, de 09:01 a 17:05), con huella
idéntica: la maqueta de la página entra entera —cabecera, filtros, tu avatar: 23
nodos `data-qa-component`—, la rejilla de resultados se queda vacía en **todas**
las búsquedas a la vez, el keep-alive sigue viendo la sesión viva cada hora, y al
cabo de unas horas se arregla solo. El aviso ya lo dice con ese nombre y añade
«la sesión no se toca», porque el correo del 31-08 recomendaba re-loguear una
sesión que llevaba 11 días perfecta.

También hay dos salidas laterales: **exportar / importar sesión** (para hacer el
login en un portátil y llevarla a un servidor sin pantalla) y la vía pública de
reuters.com, que se probó y se **descartó** — funciona sin login, pero solo
publica un 5-10 % del teletipo.

## 5. Los avisos

Si configuras `alerts.webhook_url`, el programa manda un POST JSON
`{subject, message}` cuando algo se rompe. La regla es **un aviso por avería, no
uno por ciclo**: salta en el primer fallo tras funcionar y no vuelve a sonar
hasta que se recupera y se rompe otra vez.

Hay **dos canales distintos**, y la diferencia importa:

- **`reuters` (y las demás agencias)** — «esta agencia ha dejado de traer fotos».
  Solo lo escriben las ejecuciones reales del runner. Se puede apagar por agencia.
- **`reuters-sesion`** — «hace falta que entres a mano». Lo escribe el keep-alive.
  Este **no** se filtra por agencia a propósito: no avisa de una avería del
  servicio, sino de algo que solo una persona puede arreglar.

Dos matices que importan: el veredicto de la agencia lo da el **refresco global
entero** (la agencia es buena solo si TODAS sus búsquedas fueron bien), y fuera
del lote —el botón ↻, el backfill, el alta de una búsqueda— un éxito **no
rearma** el disparador: solo el lote, que ve a la agencia completa, puede darla
por sana.

Mezclar los dos canales fue exactamente lo que provocó la tormenta de correos del
27 de agosto de 2026 (ver [REGISTRO.md](REGISTRO.md)).

## 6. Dónde vive cada cosa

- **El código de desarrollo** está en `D:\CODE\github\newsphotostalker`, que **no
  es un disco local**: es una unidad de red. Si `git` se queja de *dubious
  ownership*, hay que añadir la carpeta a `safe.directory` (una sola vez).
- **Los datos** (`data/`) viven junto al programa: la base SQLite, las fotos, el
  perfil del navegador y la clave. Copiar esa carpeta es copiarlo todo.
  Ojo: el `data/` de esta carpeta es el de **desarrollo** y está desfasado.
- **El despliegue de verdad no es este PC**: corre en un **NAS, en Docker, 24/7**,
  con el código montado desde fuera del contenedor. Actualizarlo es `git pull` +
  reiniciar el contenedor, sin reconstruir la imagen. Los datos de conexión están
  en las notas privadas del dueño, no en el repositorio. **No se entra al NAS por
  iniciativa propia: solo si él lo pide.**
- **Los paquetes** de Windows, macOS y Linux los compila GitHub Actions en cada
  etiqueta `v*`. Nadie compila a mano.

## 7. Cómo se trabaja

```sh
.venv/Scripts/python.exe -m pytest -q     # Windows
.venv/bin/python -m pytest -q             # Linux/macOS
```

Hoy son **173 pruebas** y tardan un minuto. El entorno necesita **Python 3.12**
(es el que fija la CI) y el Chromium de Playwright. Si tras un formateo el `.venv`
parece muerto, suele revivir intacto reinstalando Python 3.12 en la misma ruta;
lo que sí hay que volver a bajar es el Chromium
(`python -m playwright install chromium`).

## 8. Normas del proyecto

1. **Esta bitácora se lleva siempre al día.** Es la norma principal. Si un cambio
   altera cómo funciona el programa, dónde vive algo o cómo se trabaja, se
   actualiza este fichero **en el mismo cambio**, no después. Y se toca la fecha
   de arriba.
2. **Cada cambio se anota en [REGISTRO.md](REGISTRO.md)**: dos o tres líneas en
   castellano, lo más nuevo arriba, diciendo *qué* cambió y *por qué*.
3. **Se escribe en castellano**: comentarios, docstrings, mensajes de commit,
   avisos y documentación. (Queda código antiguo con comentarios en inglés; lo
   nuevo va en castellano.)
4. **El comentario explica el porqué, no el qué.** La costumbre de la casa es
   dejar escrito el motivo real —incluida la avería concreta y su fecha— para que
   nadie «limpie» mañana algo que está puesto a propósito. Las pruebas hacen lo
   mismo: su docstring cuenta qué se rompió en producción.
5. **Nada se da por bueno sin las pruebas en verde.** Toda corrección de una
   avería real trae una prueba que falla con el código viejo.
6. **La documentación cuenta el programa que existe**, no el que se pensaba
   hacer: si cambia el comportamiento, se tocan `MANUAL.md`, `README.md`,
   `README.es.md` y `config.example.yaml` en el mismo cambio.
7. **Los mensajes de commit son un titular en castellano** que dice el efecto
   («Los avisos admiten una postdata…»), y el cuerpo explica el porqué y qué se
   verificó.
8. **Publicar es aparte**: un commit de versión (`1.2.2`), la etiqueta `v1.2.2` y
   que la CI haga el resto.
9. **El login de Reuters no se automatiza. Nunca.** Ni «solo para probar»: cada
   intento fallido contra el muro acerca otro bloqueo de la cuenta.
10. **Secretos y datos de infraestructura, fuera del repositorio.** El
    repositorio es público: `config.local.yaml`, `secret.key` y `data/` no se
    suben, y las direcciones del NAS tampoco se escriben aquí.
11. **Al NAS no se entra sin que lo pida el dueño.**

## 9. Estado a 2026-08-28

- La última versión publicada es la **1.2.2** (28-08-2026): el arreglo de la
  tormenta de avisos, revisado, con dos ficheros nuevos de tests y los papeles
  al día. La bitácora nació también hoy. Las 173 pruebas pasan.
- La 1.2.2 **corre en el NAS** desde el 28-08 a las 22:45 (git pull + reinicio
  del contenedor; arranque limpio, planificador y keep-alive programados).
- **Cerrado hoy con los datos del NAS**: la tormenta del 27-08 fue un corte
  transitorio de Reuters de ~4 horas (15:00–19:00, «no result cards» con la
  sesión viva) que se arregló solo; los tres correos los fabricó el rearme del
  keep-alive, exactamente lo que la 1.2.2 elimina. Y la sesión lleva 9+ días
  viva sin re-login: el TTL de 24 h queda descartado (ver la sección 4).
- **Sigue abierto**: la medición pasiva de la vida de la sesión (cada aviso de
  caída dirá cuánto aguantó) y, del despliegue, la IP estable y que Chrome no
  se actualice solo.
