"""El keep-alive renueva la sesión de verdad, y no se deja engañar por el muro.

Antes cargaba la home y hacía scroll: eso solo refrescaba la cookie de DataDome
y dejaba morir el token de auth0 a las pocas horas, obligando a re-loguear a mano
sin parar. Ahora hace una BÚSQUEDA real (que fuerza la renovación del token) y
quien decide el estado es un CLASIFICADOR (viva/challenge/caida) que mira el
challenge de DataDome ANTES que la URL — porque el muro se sirve en la misma URL
y mirarla a secas anotaba éxitos falsos.

La señal de vida significa «el keep-alive corrió», no «la sesión está sana»: se
marca en todos los desenlaces, para que el vigilante solo acuse al planificador
cuando de verdad ha dejado de programar el trabajo.

Y avisa por SU canal ("reuters-sesion"), nunca por el de la agencia: tocarlo
rearmaba el aviso de «reuters ha dejado de funcionar» cada hora y lo convertía
en una tormenta de correos (27-08-2026, tres avisos idénticos en una tarde).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.ingest import keepalive


class _Page:
    def goto(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass


class FakeAdapter:
    """Adaptador de Reuters de pega: ni navegador, ni red."""

    def __init__(self, *, logged_in, challenge=False, search_boom=None, open_boom=None):
        self._logged_in = logged_in
        self._challenge = challenge
        self._search_boom = search_boom  # excepción que lanza search(), si alguna
        self._open_boom = open_boom      # ídem para open(): el keep-alive reventado
        self.requires_login = True
        self.searched = None
        self.closed = False
        self._page = _Page()

    def open(self):
        if self._open_boom:
            raise self._open_boom

    @property
    def page(self):
        return self._page

    def _looks_logged_in(self):
        return self._logged_in

    def _datadome_challenge(self):
        return self._challenge

    def estado_sesion(self):
        # Réplica del clasificador real (hay tests del de verdad más abajo):
        # el login manda; con URL de logueado, el contenido decide.
        if not self._logged_in:
            return "caida"
        if self._challenge:
            return "challenge"
        return "viva"

    def search(self, **kw):
        self.searched = kw
        if self._search_boom:
            raise self._search_boom
        return []

    def close(self):
        self.closed = True


def _engancha(monkeypatch, fake):
    """Sustituye el adaptador real y captura los dos canales de aviso y la señal.

    ``runs`` son las anotaciones en el canal de la AGENCIA (que el keep-alive ya
    no debe tocar nunca) y ``avisos`` las de su canal de SESIÓN.
    """
    monkeypatch.setattr("app.ingest.reuters.ReutersAdapter", lambda s, c: fake)
    runs: list[tuple] = []
    monkeypatch.setattr(
        keepalive.alerts, "record_run",
        lambda s, a, ok=True, error="": runs.append((a, ok, error)),
    )
    avisos: list[tuple] = []
    monkeypatch.setattr(
        keepalive.alerts, "vigila",
        lambda s, clave, ok, asunto, mensaje: avisos.append((clave, ok, asunto, mensaje)),
    )
    senales: list[str] = []
    monkeypatch.setattr(keepalive, "marca_senal", lambda s, motivo="ok": senales.append(motivo))
    return runs, senales, avisos


def test_keepalive_renueva_con_una_busqueda(monkeypatch):
    """Con sesión viva hace una búsqueda ligera —lo que renueva el token—."""
    fake = FakeAdapter(logged_in=True)
    runs, senales, avisos = _engancha(monkeypatch, fake)

    keepalive._keepalive_locked(SimpleNamespace(), SimpleNamespace())

    assert fake.searched is not None            # hizo una búsqueda real,
    assert fake.searched["kind"] == "text"
    assert fake.searched["limit"] == 1          # pero mínima
    assert runs == []                           # sin tocar el canal de la agencia
    assert [a[:2] for a in avisos] == [("reuters-sesion", True)]  # rearma el suyo
    assert senales == ["ok"]                    # y deja señal de vida
    assert fake.closed


def test_challenge_no_es_exito_aunque_la_url_enganie(monkeypatch):
    """EL caso que anotaba éxitos falsos: DataDome sirve su muro EN LA MISMA URL,
    así que la URL dice «logueado» mientras la página es el CAPTCHA. El
    clasificador debe imponerse: ni búsqueda, ni ok, ni aviso de re-login."""
    fake = FakeAdapter(logged_in=True, challenge=True)
    runs, senales, avisos = _engancha(monkeypatch, fake)

    keepalive._keepalive_locked(SimpleNamespace(), SimpleNamespace())

    assert fake.searched is None                # no llegó a buscar
    assert runs == [] and avisos == []          # ni éxito falso ni falsa alarma
    assert senales == ["challenge de DataDome"]  # pero el keep-alive corrió
    assert fake.closed


def test_en_el_login_el_muro_no_disfraza_la_caida(monkeypatch):
    """La página de login puede venir con el captcha de DataDome ENCIMA
    (visto en vivo, 08-2026). Eso no la vuelve un muro transitorio: si Reuters
    te mandó al login, la sesión no está, y hay que avisar — «challenge» aquí
    taparía la única avería que exige un humano."""
    fake = FakeAdapter(logged_in=False, challenge=True)
    runs, senales, avisos = _engancha(monkeypatch, fake)

    keepalive._keepalive_locked(SimpleNamespace(), SimpleNamespace())

    assert runs == []
    assert [a[:2] for a in avisos] == [("reuters-sesion", False)]
    assert senales == ["sesión caída"]
    assert fake.closed


def test_busqueda_que_tropieza_sin_muro_sigue_siendo_sesion_viva(monkeypatch):
    """Un tropiezo de la búsqueda de calentamiento (p. ej. cambio de maqueta) no
    es una sesión caída: la sesión ya se vio viva. Se rearma su canal, y NADA
    llega al de la agencia — el tropiezo lo reportará la búsqueda real si lo hay."""
    fake = FakeAdapter(logged_in=True, search_boom=RuntimeError("no result cards"))
    runs, senales, avisos = _engancha(monkeypatch, fake)

    keepalive._keepalive_locked(SimpleNamespace(), SimpleNamespace())

    assert runs == []
    assert [a[:2] for a in avisos] == [("reuters-sesion", True)]
    assert senales == ["ok"]


def test_busqueda_que_ve_el_login_es_sesion_CAIDA(monkeypatch):
    """La búsqueda de calentamiento ya clasifica: cuando Reuters la manda al
    login lanza SinSesionError. Reclasificar entonces con la sonda del captcha a
    secas etiquetaba «challenge» —el login de Reuters llega con el captcha
    encima, visto en vivo— y, sin captcha, dejaba el estado en «viva» y rearmaba
    el canal de la sesión justo después de que la búsqueda hubiera demostrado que
    no lo está. Manda el clasificador: el login por delante del muro."""
    from app.ingest.live_base import SinSesionError

    fake = FakeAdapter(logged_in=True)
    runs, senales, avisos = _engancha(monkeypatch, fake)

    def _al_login(**kw):
        fake.searched = kw
        fake._logged_in = False   # Reuters mandó la búsqueda al login...
        fake._challenge = True    # ...y el login viene con el captcha encima
        raise SinSesionError("Reuters mandó la búsqueda al login")

    fake.search = _al_login
    keepalive._keepalive_locked(SimpleNamespace(), SimpleNamespace())

    assert runs == []
    assert [a[:2] for a in avisos] == [("reuters-sesion", False)]
    assert senales == ["sesión caída"]


def test_busqueda_que_tropieza_POR_el_muro_se_reclasifica(monkeypatch):
    """Si el muro aparece a mitad (la búsqueda revienta y la página ya es el
    challenge), no vale anotar éxito: es un challenge."""
    fake = FakeAdapter(logged_in=True, search_boom=RuntimeError("no result cards"))
    runs, senales, avisos = _engancha(monkeypatch, fake)

    # El muro aparece justo cuando la búsqueda falla.
    def _boom(**kw):
        fake.searched = kw
        fake._challenge = True
        raise RuntimeError("no result cards")

    fake.search = _boom
    keepalive._keepalive_locked(SimpleNamespace(), SimpleNamespace())

    assert runs == [] and avisos == []          # nada de ok falso
    assert senales == ["challenge de DataDome"]


def test_sesion_muerta_de_verdad_avisa(monkeypatch):
    """Sin sesión y sin challenge: la sesión murió y hay que avisar.

    El aviso sale por el canal de la SESIÓN, con asunto propio: el de la agencia
    es de las ejecuciones reales.
    """
    fake = FakeAdapter(logged_in=False, challenge=False)
    runs, senales, avisos = _engancha(monkeypatch, fake)

    keepalive._keepalive_locked(SimpleNamespace(), SimpleNamespace())

    assert runs == []
    assert len(avisos) == 1
    clave, ok, asunto, mensaje = avisos[0]
    assert (clave, ok) == ("reuters-sesion", False)
    assert "caducado" in asunto
    assert "iniciar sesión en Reuters" in mensaje
    assert senales == ["sesión caída"]          # corrió, y encontró lo que encontró
    assert fake.searched is None
    assert fake.closed


def test_el_keepalive_nunca_escribe_en_el_canal_de_la_agencia(monkeypatch):
    """El invariante, en TODOS sus desenlaces.

    El canal "reuters" solo lo escriben las ejecuciones reales del runner. Si el
    keep-alive lo tocara —aunque fuese para anotar un éxito— rearmaría el aviso
    por flanco de la agencia cada hora, y volvería la tormenta de correos.
    """
    casos = {
        "viva": FakeAdapter(logged_in=True),
        "challenge": FakeAdapter(logged_in=True, challenge=True),
        "caida": FakeAdapter(logged_in=False),
        "tropieza": FakeAdapter(logged_in=True, search_boom=RuntimeError("no result cards")),
        "revienta": FakeAdapter(logged_in=True, open_boom=RuntimeError("el navegador no arranca")),
    }
    for nombre, fake in casos.items():
        runs, _, _ = _engancha(monkeypatch, fake)
        keepalive._keepalive_locked(SimpleNamespace(), SimpleNamespace())
        assert runs == [], f"el desenlace «{nombre}» escribió en el canal de la agencia"


def _settings_de_avisos(tmp_path):
    """Lo justo para que la máquina de estados de avisos de VERDAD funcione."""
    return SimpleNamespace(
        data_dir=tmp_path,
        alerts=SimpleNamespace(
            enabled=True,
            webhook_url="http://webhook.invalido/x",
            agencies=["reuters"],
            timeout_s=1,
            postdata=None,
        ),
    )


def _entrega_capturada(monkeypatch) -> list[str]:
    """Intercepta el envío (no se sale a la red) y devuelve los asuntos que salen."""
    from app import alerts

    enviados: list[str] = []
    monkeypatch.setattr(alerts, "_post", lambda cfg, asunto, mensaje: enviados.append(asunto) or True)
    return enviados


def test_el_keepalive_no_rearma_el_aviso_de_la_agencia(tmp_path, monkeypatch):
    """LA regresión del 27-08-2026: tres avisos idénticos en una tarde.

    El aviso de agencia es POR FLANCO —uno por avería, silencio hasta que se
    recupere—, pero el tick horario del keep-alive anotaba `record_run(ok=True)`
    y rearmaba el disparador entre fallo y fallo, así que cada tramo horario
    volvía a avisar de la MISMA avería. Aquí corre la máquina de estados de
    verdad (no un doble): con el código viejo, este test ve DOS avisos.
    """
    from app import alerts

    s = _settings_de_avisos(tmp_path)
    enviados = _entrega_capturada(monkeypatch)

    alerts.record_run(s, "reuters", ok=False, error="no result cards")
    assert len(enviados) == 1                   # la avería: un aviso

    fake = FakeAdapter(logged_in=True)          # y pasa un tick del keep-alive
    monkeypatch.setattr("app.ingest.reuters.ReutersAdapter", lambda st, c: fake)
    keepalive._keepalive_locked(s, SimpleNamespace())

    alerts.record_run(s, "reuters", ok=False, error="no result cards")
    assert len(enviados) == 1                   # la misma avería: sigue callado


def test_el_canal_de_la_sesion_vuelve_a_poder_avisar(tmp_path, monkeypatch):
    """La otra mitad del invariante: un canal que solo puede callarse tampoco sirve.

    El aviso de «sesión caducada» es por flanco, así que la SEGUNDA caducidad
    solo sale si alguien rearmó el canal entre medias. Su único rearme estaba en
    el tick del keep-alive con la sesión viva... y ese tick se salta entero
    siempre que la señal esté fresca, que es lo que dejan las búsquedas reales
    cuando todo va bien. Con el runner trabajando, el canal se quedaba clavado en
    «failing» para siempre. Ahora rearma quien PRUEBA que la sesión está viva:
    también una búsqueda real que sale bien.
    """
    s = _settings_de_avisos(tmp_path)
    enviados = _entrega_capturada(monkeypatch)

    caida = FakeAdapter(logged_in=False)
    monkeypatch.setattr("app.ingest.reuters.ReutersAdapter", lambda st, c: caida)
    keepalive._keepalive_locked(s, SimpleNamespace())
    assert len(enviados) == 1                   # la sesión caducó: un aviso

    # El humano rehace el login y el runner sigue a lo suyo, con el tick del
    # keep-alive saltándose por señal fresca.
    for _ in range(5):
        keepalive.sesion_ejercitada(s, "reuters", ok=True)
    assert len(enviados) == 1                   # rearmar no manda ningún correo

    otra_caida = FakeAdapter(logged_in=False)
    monkeypatch.setattr("app.ingest.reuters.ReutersAdapter", lambda st, c: otra_caida)
    keepalive._keepalive_locked(s, SimpleNamespace())
    assert len(enviados) == 2                   # otra caducidad: otro aviso


# --- el clasificador REAL, con páginas simuladas ----------------------------
#
# La réplica del FakeAdapter ya divergió una vez del clasificador de verdad y
# el fallo pasó de largo. Estos tests clavan el contrato del real.

INTERSTITIAL = (
    '<html><body><p id="cmsg">Please enable JS</p>'
    '<iframe src="https://geo.captcha-delivery.com/captcha/?x=1"></iframe>'
    "</body></html>"
)
LOGIN_CON_CAPTCHA = (
    "<html><body>Login | Reuters Connect"
    '<iframe src="https://geo.captcha-delivery.com/captcha/?y=2"></iframe>'
    "</body></html>"
)
# Página sana que solo lleva el TAG de vigilancia de DataDome (sin iframe).
PAGINA_SANA = (
    '<html><body>fotos<script src="https://ct.captcha-delivery.com/c.js">'
    "</script></body></html>"
)


def _adapter_real(url: str, html: str):
    from app.ingest.reuters import ReutersAdapter

    class _PaginaFalsa:
        def __init__(self, url, html):
            self.url = url
            self._html = html

        def content(self):
            return self._html

    ad = ReutersAdapter.__new__(ReutersAdapter)  # sin abrir navegador
    ad._page = _PaginaFalsa(url, html)
    return ad


def test_clasificador_real_challenge_en_url_de_logueado():
    """DataDome sirve el muro EN LA MISMA URL: la URL miente, el contenido no."""
    ad = _adapter_real("https://www.reutersconnect.com/all", INTERSTITIAL)
    assert ad.estado_sesion() == "challenge"


def test_clasificador_real_el_login_manda_aunque_haya_captcha():
    """El caso visto en vivo: /login con el captcha encima = sesión caída."""
    ad = _adapter_real(
        "https://www.reutersconnect.com/login?url64=x", LOGIN_CON_CAPTCHA
    )
    assert ad.estado_sesion() == "caida"


def test_clasificador_real_viva_aunque_lleve_el_tag_de_datadome():
    """El tag de vigilancia (sin iframe de captcha) no es un challenge."""
    ad = _adapter_real("https://www.reutersconnect.com/all", PAGINA_SANA)
    assert ad.estado_sesion() == "viva"


def test_sin_perfil_no_hay_sesion_guardada(tmp_path):
    """Perfil inexistente o vacío = nadie ha hecho login aquí."""
    s = SimpleNamespace(
        playwright=SimpleNamespace(user_data_dir=str(tmp_path / "browser")),
        data_dir=tmp_path,
    )
    assert keepalive.hay_sesion_guardada(s) is False       # no existe
    (tmp_path / "browser" / "reuters").mkdir(parents=True)
    assert keepalive.hay_sesion_guardada(s) is False       # existe pero vacío
    (tmp_path / "browser" / "reuters" / "Local State").write_text("{}")
    assert keepalive.hay_sesion_guardada(s) is True        # con contenido: sí


def _settings_con_perfil(tmp_path, **extra):
    perfil = tmp_path / "browser" / "reuters"
    perfil.mkdir(parents=True, exist_ok=True)
    (perfil / "Local State").write_text("{}")
    return SimpleNamespace(
        playwright=SimpleNamespace(user_data_dir=str(tmp_path / "browser")),
        data_dir=tmp_path,
        reuters_keepalive_minutes=60,
        credentials_for=lambda a: SimpleNamespace(enabled=True),  # SIN password
        **extra,
    )


def test_keepalive_corre_sin_contrasena(tmp_path, monkeypatch):
    """El keep-alive ya no exige credenciales en la config: basta el perfil.

    Antes pedía usuario+contraseña, lo que obligaba a guardar la contraseña en
    un fichero solo para encenderlo — y el programa ya no la usa para nada.
    """
    s = _settings_con_perfil(tmp_path)
    monkeypatch.setattr(keepalive, "get_settings", lambda: s)

    llamadas = []
    from app.ingest import live_base

    monkeypatch.setattr(live_base, "en_hilo_sin_bucle", lambda f, *a, **k: llamadas.append(f))
    keepalive.keepalive_reuters()
    assert llamadas == [keepalive._keepalive_locked]

    # Y sin perfil (nadie ha hecho login) no arranca nada.
    llamadas.clear()
    s2 = SimpleNamespace(
        playwright=SimpleNamespace(user_data_dir=str(tmp_path / "otro")),
        data_dir=tmp_path / "otro",
        reuters_keepalive_minutes=60,
        credentials_for=lambda a: SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(keepalive, "get_settings", lambda: s2)
    keepalive.keepalive_reuters()
    assert llamadas == []


def test_tick_se_salta_si_la_senal_es_fresca(tmp_path, monkeypatch):
    """Si una búsqueda real (o el tick anterior) acaba de ejercitar la sesión,
    lanzar otro navegador no aporta nada: el tick se lo ahorra."""
    s = _settings_con_perfil(tmp_path)
    monkeypatch.setattr(keepalive, "get_settings", lambda: s)
    llamadas = []
    from app.ingest import live_base

    monkeypatch.setattr(live_base, "en_hilo_sin_bucle", lambda f, *a, **k: llamadas.append(f))

    keepalive.marca_senal(s, motivo="búsqueda real")  # ahora mismo
    keepalive.keepalive_reuters()
    assert llamadas == []                              # señal fresca: sin trabajo


def test_una_busqueda_real_cuenta_como_senal(tmp_path):
    """El runner avisa al keep-alive: un run bueno de Reuters ES su trabajo.

    Y, por el mismo argumento, PRUEBA que la sesión sigue viva: rearma su canal.
    """
    from app import alerts

    s = _settings_de_avisos(tmp_path)
    keepalive.sesion_ejercitada(s, "reuters", ok=True)
    assert keepalive.ultima_senal(s) is not None
    assert alerts.status(s, keepalive.CLAVE_SESION) == "ok"

    # Ni las demás agencias ni los runs fallidos cuentan.
    s2 = SimpleNamespace(data_dir=tmp_path / "b")
    keepalive.sesion_ejercitada(s2, "ap", ok=True)
    keepalive.sesion_ejercitada(s2, "reuters", ok=False)
    assert keepalive.ultima_senal(s2) is None


def test_mide_cuanto_duro_la_sesion(tmp_path, monkeypatch):
    """El aviso de sesión caída dice cuántos días aguantó desde el último login."""
    s = SimpleNamespace(data_dir=tmp_path)

    # Sin fecha anotada: no hay medida, y no debe romper nada.
    assert keepalive.dias_desde_login_humano(s) is None

    keepalive.marca_login_humano(s, via="login manual")
    edad = keepalive.dias_desde_login_humano(s)
    assert edad is not None and edad < 0.01     # recién anotado

    fake = FakeAdapter(logged_in=False, challenge=False)
    _, _, avisos = _engancha(monkeypatch, fake)
    keepalive._keepalive_locked(s, SimpleNamespace())
    assert len(avisos) == 1
    assert "aguantó" in avisos[0][3]            # el aviso lleva la duración
    assert "días" in avisos[0][3]


def test_un_fichero_de_login_corrupto_no_rompe(tmp_path):
    s = SimpleNamespace(data_dir=tmp_path)
    keepalive._ruta_login_humano(s).write_text("{no es json")
    assert keepalive.dias_desde_login_humano(s) is None


def test_keepalive_activo_por_defecto(tmp_path, monkeypatch):
    """Sin la clave en la config, el keep-alive queda ENCENDIDO (antes: apagado)."""
    from app.config import reload_settings

    cfg = tmp_path / "config.yaml"
    cfg.write_text("mode: mock\n", encoding="utf-8")
    monkeypatch.setenv("APP_CONFIG", str(cfg))
    try:
        s = reload_settings()
        assert s.reuters_keepalive_minutes == 60
    finally:
        monkeypatch.delenv("APP_CONFIG", raising=False)
        reload_settings()  # restaura la config real en la caché global
