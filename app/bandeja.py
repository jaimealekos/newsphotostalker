"""El icono junto al reloj: la única cara visible del programa.

Antes, la ventana de consola **era** el programa: cerrarla lo paraba. Eso
obligaba a dejar a la vista una ventana negra con las tripas —peticiones HTTP,
avisos de Chrome, trazas— que no le dice nada a quien solo quiere ver fotos, y
que además invita a cerrarla «para quitarla de en medio», con lo que el
programa moría sin que nadie entendiera por qué.

Ahora no hay ventana. Queda un icono junto al reloj (en macOS, en la barra de
menús) con dos entradas: abrir el panel y salir. El programa sigue funcionando
aunque cierres el navegador —descargando fotos a su hora—, que es justo lo que
se espera de algo que vigila agencias de noticias.

En Linux se deja fuera a propósito: hay media docena de escritorios con
bandejas distintas, y allí esto se usa sobre todo en servidores sin pantalla,
donde lo natural es un servicio (ver ``deploy/``).
"""

from __future__ import annotations

import logging
import sys
import webbrowser

log = logging.getLogger("bandeja")

#: Tamaño del icono. 64 px va bien en Windows y en la barra de macOS; el
#: original es de 256 y se reduce con buen filtro.
LADO = 64


def disponible() -> bool:
    """¿Se puede poner el icono en este sistema?

    Nunca lanza: si algo falta, el programa tiene que poder seguir arrancando
    en modo consola en vez de no arrancar.
    """
    if sys.platform.startswith("linux"):
        return False
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        log.info("sin icono de bandeja (%s); se sigue en modo consola", exc)
        return False
    return True


def _imagen():
    """El icono del programa, o uno dibujado al vuelo si no estuviera."""
    from PIL import Image, ImageDraw

    from .config import BUNDLE_DIR

    ruta = BUNDLE_DIR / "app" / "static" / "icono.png"
    try:
        return Image.open(ruta).convert("RGBA").resize((LADO, LADO), Image.LANCZOS)
    except Exception:  # noqa: BLE001 - el icono es lo de menos; no puede tumbar el arranque
        img = Image.new("RGBA", (LADO, LADO), (0, 0, 0, 0))
        dibujo = ImageDraw.Draw(img)
        dibujo.ellipse((6, 6, LADO - 6, LADO - 6), fill=(165, 28, 28, 255))
        return img


def arrancar(url: str, al_salir) -> None:
    """Pone el icono y **bloquea** hasta que se elige Salir.

    Bloquea a propósito: en macOS la barra de menús solo se puede manejar desde
    el hilo principal, así que el icono se queda con él y el servidor web corre
    en un hilo aparte. Al salir se llama a ``al_salir()`` para pararlo con
    orden.
    """
    import pystray

    def abrir(icono=None, elemento=None):
        webbrowser.open(url)

    def salir(icono, elemento):
        icono.visible = False
        icono.stop()

    menu = pystray.Menu(
        # `default=True`: es lo que pasa al pulsar el icono, no solo al elegirlo
        # en el menú. Es lo que espera cualquiera.
        pystray.MenuItem("Abrir panel", abrir, default=True),
        pystray.MenuItem("Salir", salir),
    )
    icono = pystray.Icon("newsphotostalker", _imagen(), "newsphotostalker", menu)
    log.info("icono en la bandeja; el panel está en %s", url)
    icono.run()
    al_salir()
