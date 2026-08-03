"""El trabajo de Playwright nunca corre dentro de un bucle de asyncio.

La API de bloqueo de Playwright se niega a arrancar —«Sync API inside the
asyncio loop»— si en el hilo actual hay un bucle corriendo. Pasó de verdad al
importar una sesión, y volvió a aparecer al iniciar sesión en Reuters desde el
programa empaquetado, en un camino que no se reprodujo en desarrollo.

En vez de perseguir cada sitio desde el que se llama, el trabajo se envuelve en
un hilo recién creado, que por definición no tiene bucle. Esto lo comprueba.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.ingest.live_base import en_hilo_sin_bucle


def _hay_bucle_aqui() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def test_dentro_de_un_bucle_el_trabajo_corre_fuera_de_el():
    """El caso que fallaba: llamar desde una corrutina."""

    async def desde_el_bucle():
        assert _hay_bucle_aqui(), "la prueba necesita un bucle corriendo aquí"
        # Lo que ve la función envuelta es lo que vería Playwright.
        return en_hilo_sin_bucle(_hay_bucle_aqui)

    assert asyncio.run(desde_el_bucle()) is False


def test_sin_bucle_tambien_corre_en_otro_hilo():
    assert _hay_bucle_aqui() is False
    assert en_hilo_sin_bucle(_hay_bucle_aqui) is False
    assert en_hilo_sin_bucle(threading.current_thread).name == "playwright"


def test_devuelve_el_resultado_y_pasa_los_argumentos():
    assert en_hilo_sin_bucle(lambda a, b=0: a + b, 2, b=3) == 5


def test_las_excepciones_llegan_al_que_llama():
    """Un fallo dentro del hilo no puede desaparecer en silencio."""

    def revienta():
        raise ValueError("algo salió mal")

    with pytest.raises(ValueError, match="algo salió mal"):
        en_hilo_sin_bucle(revienta)


def test_tambien_se_propagan_desde_dentro_de_un_bucle():
    async def desde_el_bucle():
        return en_hilo_sin_bucle(lambda: 1 / 0)

    with pytest.raises(ZeroDivisionError):
        asyncio.run(desde_el_bucle())
