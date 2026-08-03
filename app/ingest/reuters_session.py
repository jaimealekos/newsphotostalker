"""Llevarse la sesión de Reuters de un equipo a otro.

El problema que resuelve: el login de Reuters necesita una ventana de navegador
y una persona (DataDome no se automatiza), pero mucha gente ejecuta esto en un
servidor Linux sin pantalla. La salida es hacer el login donde SÍ hay pantalla
—tu portátil— y traerse la sesión.

Cómo, y por qué así: no se copia el perfil del navegador. Chromium cifra las
cookies con claves del sistema operativo (DPAPI en Windows, el llavero en
macOS), así que un perfil copiado a otra máquina llega con las cookies
ilegibles. Lo que se exporta es el ``storage_state`` de Playwright: cookies y
almacenamiento local **ya descifrados**, en un JSON portable entre sistemas.

    Portátil:  ajustes → exportar sesión   → sesion-reuters.json
    Servidor:  ajustes → importar sesión   → y ya busca sin pedir nada
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import get_settings
from .reuters import ReutersAdapter

log = logging.getLogger("reuters-session")

#: Dominios cuyas cookies importan para la sesión (el resto es ruido).
DOMINIOS = ("reutersconnect.com", "thomsonreuters.com", "datadome")


class SessionError(RuntimeError):
    pass


def export_state() -> dict[str, Any]:
    """Cookies y almacenamiento local de la sesión actual, listos para viajar.

    Falla en claro si no hay sesión: exportar una vacía y descubrirlo tres días
    después en el servidor sería peor.
    """
    with _adapter() as adapter:
        page = adapter.page
        page.goto("https://www.reutersconnect.com/all", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        if not adapter._looks_logged_in():
            raise SessionError(
                "aquí tampoco hay sesión de Reuters que exportar: inicia sesión "
                "primero con «iniciar sesión en Reuters»"
            )
        estado = adapter._context.storage_state()

    estado["cookies"] = [c for c in estado.get("cookies", []) if _relevante(c.get("domain", ""))]
    log.info("sesión exportada: %s cookies", len(estado["cookies"]))
    return estado


def import_state(estado: dict[str, Any]) -> str:
    """Instala una sesión exportada en el perfil de este equipo.

    Devuelve un mensaje para el panel. Comprueba de verdad que ha quedado
    iniciada: si no, avisa en vez de dejarlo pasar en silencio.
    """
    cookies = estado.get("cookies") if isinstance(estado, dict) else None
    if not isinstance(cookies, list) or not cookies:
        raise SessionError("el fichero no parece una sesión exportada (no trae cookies)")

    with _adapter() as adapter:
        contexto = adapter._context
        try:
            contexto.add_cookies([_limpia(c) for c in cookies if _relevante(c.get("domain", ""))])
        except Exception as exc:  # noqa: BLE001
            raise SessionError(f"no se pudieron instalar las cookies: {exc}") from exc

        page = adapter.page
        page.goto("https://www.reutersconnect.com/", wait_until="domcontentloaded")
        _restaura_local_storage(page, estado.get("origins") or [])

        page.goto("https://www.reutersconnect.com/all", wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        if not adapter._looks_logged_in():
            raise SessionError(
                "la sesión importada no vale (probablemente ha caducado); "
                "vuelve a exportarla en el equipo donde funciona"
            )
    return f"sesión importada ({len(cookies)} cookies): Reuters ya no pedirá login aquí"


def _restaura_local_storage(page, origins: list[dict]) -> None:
    """El token de sesión de Reuters vive en localStorage, no solo en cookies."""
    for origin in origins:
        if not _relevante(str(origin.get("origin", ""))):
            continue
        for item in origin.get("localStorage") or []:
            try:
                page.evaluate(
                    "([k, v]) => localStorage.setItem(k, v)",
                    [item.get("name"), item.get("value")],
                )
            except Exception:  # noqa: BLE001 - una clave suelta no aborta el resto
                continue


def _relevante(dominio: str) -> bool:
    return any(d in dominio for d in DOMINIOS)


def _limpia(cookie: dict) -> dict:
    """Deja la cookie con los campos que acepta ``add_cookies``."""
    permitidos = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")
    limpia = {k: v for k, v in cookie.items() if k in permitidos and v is not None}
    if limpia.get("sameSite") not in ("Strict", "Lax", "None"):
        limpia.pop("sameSite", None)
    return limpia


#: Espera máxima a que termine una búsqueda antes de rendirse (comparten perfil).
LOCK_WAIT_SECONDS = 90


def _adapter():
    """Adaptador de Reuters sin login automático y sin ventana."""
    settings = get_settings()
    adapter = ReutersAdapter(settings, settings.credentials_for("reuters"))
    adapter.requires_login = False
    return adapter


def con_navegador_libre(funcion, *args):
    """Ejecuta algo que necesita el navegador sin pisar una búsqueda en curso.

    Chromium no admite dos instancias sobre el mismo perfil, así que se comparte
    el lock del runner. Si hay una ejecución larga, se falla en claro en vez de
    reventar con un error de perfil bloqueado.
    """
    from .runner import _RUN_LOCK

    if not _RUN_LOCK.acquire(timeout=LOCK_WAIT_SECONDS):
        raise SessionError("hay una búsqueda en marcha; inténtalo en un minuto")
    try:
        return funcion(*args)
    finally:
        _RUN_LOCK.release()


def to_json(estado: dict[str, Any]) -> str:
    return json.dumps(estado, indent=1, ensure_ascii=False)


def from_json(texto: str | bytes) -> dict[str, Any]:
    if isinstance(texto, bytes):
        texto = texto.decode("utf-8", errors="replace")
    try:
        estado = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise SessionError(f"el fichero no es un JSON válido: {exc}") from exc
    if not isinstance(estado, dict):
        raise SessionError("el fichero no tiene la forma de una sesión exportada")
    return estado
