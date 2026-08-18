<p align="right"><a href="README.md">English</a> · <b>Español</b></p>

<h1 align="center">newsphotostalker</h1>

<p align="center">
  <a href="../../releases"><b>Descargar</b></a> ·
  <a href="MANUAL.md">Manual completo</a>
</p>

<p align="center">
  <img src="docs/screenshots/01-dashboard.png" alt="El panel: una fila por búsqueda, con la marca roja donde han entrado fotos nuevas" width="860">
</p>

---

Monitorear el trabajo de los fotógrafos de las agencias de prensa internacionales
(**AP, Reuters, AFP, Getty**) ha sido una de las vías tradicionales de estudio del
oficio de fotoperiodista.

En papel, terminales específicos o en la web desde la llegada de Internet, muchos
hemos intentado separar el trigo de la cizaña para aprender de los mejores y,
también, analizar los fallos de los demás.

Siempre ha sido incómodo tener que guardar favoritos de búsquedas y perder tiempo
navegando a través de las webs de las agencias, que no siempre tienen un diseño
limpio, y a veces parece que empeoren con el tiempo.

**News Photo Stalker** optimiza este trabajo al máximo: una sola pantalla igual
para las cuatro agencias, automatizar búsquedas por fotógrafo o etiquetas,
ordenarlas y actualizar manual o automáticamente.

Disponible gratis para **Windows, Mac y Linux** bajo licencia MIT.

---

## Qué hace

Le das el **nombre de un fotógrafo** o una **búsqueda de texto** en cualquiera de
las cuatro agencias. Busca según una cadencia, descarga lo nuevo y lo guarda en tu
disco con pie de foto, autor, crédito y fecha.

- 🔴 **Una luz por búsqueda** cuando entran fotos, con la fecha de la última. Se
  apaga cuando abres *esa* búsqueda, no cuando echas un vistazo al panel.
- ⇅ **Panel ordenable** a mano, con separadores para agrupar.
- ⤓ **Rellenar histórico**: baja hacia atrás hasta donde llegue tu retención.
- 🗑️ **Retención** por tiempo (meses) o por espacio (MB), con purga automática.
- 🖥️ Funciona en **Windows, macOS y Linux**. Sin instalar nada.

---

## Instalación

Descarga el paquete de tu sistema desde [releases](../../releases), descomprímelo y
ejecútalo. Python y todo lo demás viajan dentro.

| Sistema | Qué hacer |
|---|---|
| **Windows** | Doble clic en `newsphotostalker.bat`. |
| **macOS** | Doble clic en `newsphotostalker.app`. La primera vez, macOS lo bloquea: ver abajo. |
| **Linux** | `./newsphotostalker` — o instálalo con la orden de abajo. |

```sh
curl -fsSL https://raw.githubusercontent.com/jaimealekos/newsphotostalker/main/install.sh | sh
```

No se abre ninguna ventana: el programa vive en el **icono junto al reloj** (en
macOS, en la barra de menús) y el panel se abre en tu navegador; desde el icono
se reabre el panel y se sale. Entra con **`admin` / `admin`** y cámbialo en
*ajustes*. Si algo falla, el lanzador **`(consola)`** arranca con ventana y
enseña el detalle, y todo queda también en `data/newsphotostalker.log`.

### macOS: el aviso de la primera vez

Este programa no está firmado con una cuenta de desarrollador de Apple (cuesta
99 $ al año), así que macOS lo bloquea la primera vez. **No es que esté roto.**
Le pasa igual a cualquier herramienta pequeña que te descargues.

1. Doble clic en `newsphotostalker.app`. Saldrá un aviso de que no se puede
   abrir. Dale a **Cancelar** (no a la papelera).
2. Ve a **Ajustes del Sistema → Privacidad y seguridad**, baja hasta abajo y
   pulsa **«Abrir igualmente»** junto al nombre del programa.
3. Vuelve a hacer doble clic y confirma **«Abrir»**.

> En macOS 15 (Sequoia) y posteriores, el viejo truco de *clic derecho → Abrir*
> **ya no funciona**: hay que pasar por Ajustes del Sistema. Si te sale una sola
> opción de «Abrir» y no llegas a ninguna parte, es esto.

¿Prefieres una orden y acabar? Abre **Terminal**, escribe `cd ` (con el espacio),
arrastra ahí la carpeta del programa desde el Finder, pulsa Intro y luego:

```sh
xattr -dr com.apple.quarantine . && ./newsphotostalker
```

Eso quita la marca de cuarentena a **toda** la carpeta, que es lo que importa: el
programa lleva dentro un navegador y sus bibliotecas, y basta con que una pieza
siga marcada para que el arranque falle sin decir nada.

Tus fotos, tu base de datos y la sesión del navegador se crean en una carpeta
**`data/` junto al lanzador**. Copia esa carpeta y tendrás copia de todo. No lo
descomprimas dentro de `C:\Archivos de programa`, que Windows no deja escribir ahí.

---

## Las agencias

Tres de las cuatro no necesitan cuenta. Solo Reuters.

| Agencia | Cuenta | Cómo se baja | Se guarda a |
|---|---|---|---|
| **AP** | no hace falta | API de búsqueda anónima | 1024 px |
| **Getty** | no hace falta | páginas de búsqueda | 2048 px |
| **AFP** | no hace falta | igual, vía la distribución de Getty | 2048 px |
| **Reuters** | **la tuya** | navegador con tu sesión | 640 px |

Son las previews más grandes que sirve cada agencia sin licencia, con su marca de
agua. Esta herramienta **encuentra y vigila** el trabajo; la licencia es cosa tuya
y de la agencia.

<p align="center">
  <img src="docs/screenshots/02-reuters.png" alt="Reuters: Alejandro Martínez Vélez" width="860"><br>
  <em>Reuters — Alejandro Martínez Vélez</em>
</p>

<p align="center">
  <img src="docs/screenshots/03-getty.png" alt="Getty Images: Pablo Blázquez Domínguez" width="860"><br>
  <em>Getty Images — Pablo Blázquez Domínguez</em>
</p>

### Entrar en Reuters

Reuters Connect exige sesión y está tras un muro anti-bot, así que entras **a mano
una vez** desde *ajustes → iniciar sesión en Reuters*. Se abre tu navegador normal,
inicias sesión y, al cerrar la ventana, la sesión se comprueba y se guarda sola.
**Es la única ventana que verás**: a partir de ahí las búsquedas corren sin abrir
nada. El programa **nunca teclea tu contraseña** —el login siempre lo haces tú— y
mantiene la sesión viva él solo; cuando Reuters la caduque, te lo dice y repites
el login: un minuto. En un servidor sin pantalla, entra en tu portátil y trae la
sesión con *exportar sesión* / *importar sesión*. Todo el detalle, en el
[manual](MANUAL.md).

---

## Bajo el capó

Python, [FastAPI](https://fastapi.tiangolo.com/), SQLite y
[Playwright](https://playwright.dev/) (solo para Reuters). Sin cuenta, sin clave de
API y sin telemetría: habla con las cuatro agencias y con nada más.

```sh
git clone https://github.com/jaimealekos/newsphotostalker.git
cd newsphotostalker
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.yaml config.local.yaml
python run.py
```

Los paquetes de los tres sistemas los compila y prueba
[GitHub Actions](.github/workflows/release.yml) en cada etiqueta `v*`: no compila
nadie, ni tú ni quien lo descarga. El manual completo está en [MANUAL.md](MANUAL.md).

## Licencia

[MIT](LICENSE). Libre para usar, modificar y compartir.

---

<p align="center"><sub>
newsphotostalker no está afiliado a AP, Reuters, AFP ni Getty Images.<br>
Las fotografías pertenecen a sus autores y a las agencias.
</sub></p>
