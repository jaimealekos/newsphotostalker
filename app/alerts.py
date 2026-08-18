"""Aviso por flanco cuando una agencia deja de funcionar.

El runner llama a :func:`record_run` al final de cada ejecución. La máquina de
estados por agencia vive en ``data/alert_state.json`` (persiste entre
reinicios del contenedor):

* run fallido y la agencia estaba bien -> se envía UN aviso y se marca
  ``failing`` (si el envío falla, no se marca: el siguiente run fallido
  reintenta el aviso).
* runs fallidos sucesivos -> silencio (ya se avisó de este tramo).
* run bueno -> estado ``ok``; el disparador queda rearmado.

La entrega es un POST JSON ``{subject, message}`` al webhook configurado
(``alerts.webhook_url``; apúntalo a cualquier flujo que reenvíe por email).
Se usa urllib de la stdlib a propósito: no merece una dependencia más.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from .config import Settings

log = logging.getLogger("alerts")

# El runner serializa los runs, pero el lock hace el módulo seguro por sí solo.
_LOCK = Lock()


def record_run(settings: Settings, agency: str, ok: bool, error: str | None = None) -> None:
    """Registra el resultado de un run y dispara el aviso si toca."""
    cfg = settings.alerts
    if not cfg.enabled or not cfg.webhook_url or agency not in cfg.agencies:
        return
    _flanco(settings, agency, ok, error, lambda: _send(cfg, agency, error or "(sin detalle)"))


def vigila(settings: Settings, clave: str, ok: bool, asunto: str, mensaje: str) -> None:
    """Igual que :func:`record_run`, pero para algo que no es una agencia.

    Lo usa el vigilante del keep-alive. Comparte fichero de estado y máquina de
    estados —un aviso por tramo, rearmado al recuperarse— para que no haya dos
    formas distintas de avisar que puedan divergir.
    """
    cfg = settings.alerts
    if not cfg.enabled or not cfg.webhook_url:
        return
    _flanco(settings, clave, ok, None if ok else mensaje, lambda: _post(cfg, asunto, mensaje))


def _flanco(settings: Settings, clave: str, ok: bool, detalle: str | None, enviar) -> None:
    """La máquina de estados del aviso por flanco, común a todo lo vigilado."""
    path = _state_path(settings)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _LOCK:
        state = _load(path)
        entry = state.get(clave) or {}
        if ok:
            if entry.get("status") == "failing":
                log.info("%s vuelve a funcionar; disparador rearmado", clave)
            state[clave] = {"status": "ok", "since": now}
            _save(path, state)
            return

        if entry.get("status") == "failing":
            return  # ya se avisó de este tramo de fallos

        if enviar():
            state[clave] = {"status": "failing", "since": now, "first_error": detalle}
            _save(path, state)
        else:
            log.error("no se pudo entregar el aviso de %s; se reintentará", clave)


def status(settings: Settings, agency: str) -> str | None:
    """Estado actual guardado de una agencia: 'ok', 'failing' o None."""
    entry = _load(_state_path(settings)).get(agency) or {}
    return entry.get("status")


def _send(cfg, agency: str, error: str) -> bool:
    subject = f"[newsphotostalker] {agency} ha dejado de funcionar"
    message = (
        f"La ingesta de {agency} ha fallado y no se reintentará avisar hasta "
        f"que vuelva a funcionar y falle de nuevo.\n\n"
        f"Error: {error}\n\n"
        "Revisa la actividad en el panel. Ya se reintentó una vez antes de dar "
        "la ejecución por fallida, así que no es un tropiezo aislado. Si es la "
        "sesión de Reuters, vuelve a iniciar sesión (ver el README)."
    )
    return _post(cfg, subject, message)


def _post(cfg, subject: str, message: str) -> bool:
    """Entrega un aviso por el webhook. True si el destinatario lo aceptó."""
    # La coletilla configurada (alerts.postdata) viaja al final de todo aviso:
    # es donde el dueño escribe qué hacer al recibirlo. Se añade aquí, en el
    # único punto de salida, para que ningún aviso pueda olvidarse de ella.
    if getattr(cfg, "postdata", None):
        message = f"{message}\n\n{cfg.postdata}"
    body = json.dumps({"subject": subject, "message": message}).encode()
    req = urllib.request.Request(
        cfg.webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
            if 200 <= resp.status < 300:
                log.warning("aviso enviado: %s (%s)", subject, resp.status)
                return True
            log.error("webhook de alertas devolvio %s", resp.status)
    except Exception as exc:  # noqa: BLE001
        log.error("webhook de alertas inaccesible: %s", exc)
    return False


def _state_path(settings: Settings) -> Path:
    return settings.data_dir / "alert_state.json"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)
