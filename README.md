# 📸 newsphotostalker

> Vigila las novedades de **fotógrafos** y **temas** en **Associated Press,
> Reuters, AFP y Getty Images** desde un panel web. Busca periódicamente, descarga
> las fotos nuevas con sus metadatos y aplica límites de almacenamiento.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

<p align="center">
  <img src="docs/screenshots/03-getty.png" alt="Vista de una búsqueda en newsphotostalker" width="820">
</p>

---

## ¿Qué hace?

Le dices qué fotógrafos o qué temas quieres seguir en las grandes agencias, y
newsphotostalker lo revisa solo cada cierto tiempo, descarga lo nuevo y lo
organiza en una galería. Ideal para no perderte el trabajo de un fotógrafo o la
cobertura de un tema.

Funciona en **dos capas**:

1. **Ingesta** — cada búsqueda se ejecuta según una cadencia y baja las novedades
   respecto de la última vez. Tres caminos según la agencia:
   - **AP** — API de búsqueda anónima (sin login).
   - **Getty / AFP** — páginas de búsqueda por HTTP (sin login; AFP se restringe a su colección).
   - **Reuters** — navegador con tu sesión iniciada (login humano; ver más abajo).
2. **Panel web** — añadir, editar, pausar, borrar y ejecutar búsquedas al instante,
   con pestañas de **actividad** (historial) y **ajustes**, galería paginada por
   búsqueda y **relleno de histórico** bajo demanda.

## ✨ Características

- 🔎 Búsquedas por **fotógrafo** o por **texto**, en las 4 agencias.
- 🖼️ Galería por búsqueda con pie de foto, autor, crédito y fecha.
- 🟢 **Luz de novedades por búsqueda**: se enciende cuando entran fotos y se apaga
  al abrir *esa* búsqueda, con la fecha de la última novedad al lado.
- ⇅ **Panel ordenable**: coloca las búsquedas a mano y agrúpalas con separadores.
- ⏱️ **Refresco global** configurable (cada X horas/días, a partir de una hora),
  más ejecución manual de una búsqueda al momento.
- ⤓ **Rellenar histórico**: descarga hacia atrás hasta el límite de retención.
- 🗑️ **Retención** por tiempo (meses) o por espacio (MB), con purga automática.
- 🔒 **Un solo usuario**, con login y contraseña.
- 🔔 Aviso opcional (webhook) cuando una agencia deja de funcionar.
- 🖥️ Corre en **Windows** (a demanda, doble clic) o como **servidor 24/7** (Docker).

## 🖼️ Las tres búsquedas de ejemplo

Al sembrar (`python -m scripts.seed`) se crean tres búsquedas que muestran los tres
caminos de ingesta:

| Agencia | Tipo | Consulta |
|---|---|---|
| **Reuters** | Fotógrafo | Alejandro Martínez Vélez |
| **Getty** | Fotógrafo | Pablo Blázquez Domínguez |
| **AP** | Texto | APTOPIX |

<p align="center">
  <img src="docs/screenshots/01-dashboard.png" alt="Panel con las tres búsquedas" width="820">
</p>

<table>
  <tr>
    <td><img src="docs/screenshots/02-reuters.png" alt="Reuters · Alejandro Martínez Vélez"></td>
    <td><img src="docs/screenshots/04-ap.png" alt="AP · APTOPIX"></td>
  </tr>
</table>

## 🚀 Puesta en marcha

Necesitas **Python 3.10+**. Para el modo real de Reuters, **Google Chrome** instalado.

```bash
git clone https://github.com/jaimealekos/newsphotostalker.git
cd newsphotostalker
python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config.example.yaml config.local.yaml  # arranca en modo "mock" (sin credenciales)
python -m scripts.seed                     # siembra las 3 búsquedas de ejemplo
uvicorn app.main:app                        # http://127.0.0.1:8000
```

Entra con **`admin` / `admin`** (cámbialo en *ajustes → tu cuenta*). En modo `mock`
las fotos son sintéticas, así que puedes probar TODO el sistema sin credenciales.
Cuando quieras las de verdad, pon `mode: live` en `config.local.yaml`.

### En Windows (a demanda)

Doble clic en **`arrancar_servidor.bat`**: arranca el servidor, abre el navegador
y deja la ventana abierta (cerrarla detiene el servidor).

### Como servidor 24/7 (Docker)

Ver [`deploy/`](deploy/) para la imagen Docker (con Xvfb para el navegador de
Reuters), `docker-compose`, y plantillas de systemd + nginx.

## 🔐 Reuters (login)

Reuters Connect exige sesión iniciada y está tras el muro anti-bot **DataDome**,
así que este adaptador conduce un **navegador real con tu login**. El login lo
haces **tú, a mano**, una vez:

```bash
python -m scripts.login_reuters       # (o login_reuters.bat en Windows)
```

Se abre una ventana de Chrome en la página de login; entras (email, contraseña y
el deslizador si aparece) y la sesión queda guardada para las siguientes veces.
**Es la única vez que verás una ventana**: las ejecuciones normales van en
headless y no abren nada. **AP, Getty y AFP no necesitan login.**

## ⚙️ Configuración

Todo en `config.local.yaml` (gitignored). Lo esencial: `mode` (mock/live) y las
credenciales de Reuters. `playwright.executable_path` puede quedarse en `null`: en
Windows se usa solo el Google Chrome instalado. Ver comentarios en
[`config.example.yaml`](config.example.yaml).

El **refresco** (cada cuánto se revisan todas las búsquedas y a qué hora) y las
**fotos por página** se configuran desde *ajustes* en el panel.

## 🧪 Tests

```bash
python -m pytest -q
```

Cubren retención, adaptador mock, normalización de formularios, ruteo de
credenciales, construcción de consultas de los adaptadores en vivo y el pipeline
completo de extremo a extremo.

## 📚 Más

- [MANUAL.md](MANUAL.md) — guía de uso detallada.
- [CONTRIBUTING.md](CONTRIBUTING.md) — cómo contribuir.

## ⚠️ Uso responsable

Herramienta para uso personal. Depende del maquetado y las APIs de terceros, que
pueden cambiar o romperse en cualquier momento. Respeta los **Términos de Servicio**
de cada agencia y usa tus **propias cuentas** (Reuters requiere tu sesión). Las
previews que se descargan llevan la **marca de agua** de la agencia. No lo uses
para redistribuir contenido con derechos.

## 📄 Licencia

[MIT](LICENSE) © 2026 Jaime Alekos
