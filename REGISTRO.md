# Registro de cambios

> Qué ha cambiado y por qué, **lo más nuevo arriba**. Dos o tres líneas por
> cambio, en castellano. Si necesitas el contexto del proyecto entero, empieza
> por la [BITACORA.md](BITACORA.md).
>
> Norma: **cada cambio se anota aquí**, en el mismo momento en que se hace.

---

## v1.2.2 — 2026-08-28

### Se acaba la tormenta de avisos, y los avisos dicen qué pasó

El 27 de agosto llegaron tres correos idénticos en una tarde, todos con el mismo
texto inútil («no result cards… Timeout 45000ms»). Eran tres averías distintas
de fondo, y se arreglan por separado:

- **El keep-alive tiene ahora su propio canal de avisos** (`reuters-sesion`).
  Antes anotaba cada hora un «Reuters funciona» en el canal de la agencia, lo que
  rearmaba el aviso una y otra vez y convertía «un aviso por avería» en «uno por
  tramo». Ahora el canal de la agencia solo lo escriben las búsquedas de verdad.
- **El aviso de la agencia sale una vez por ciclo, no una por búsqueda.** El
  disparador es por agencia, pero se llamaba por búsqueda: con dos búsquedas de
  Reuters —una rota y otra sana— cada ciclo avisaba del fallo y acto seguido lo
  rearmaba con el éxito de la hermana. Ahora la agencia se da por buena solo si
  **todas** sus búsquedas fueron bien.
- **La búsqueda de Reuters clasifica el fallo antes de reventar**: sesión
  caducada (no se reintenta, y dice cuántos días aguantó), muro de DataDome
  (transitorio, se reintenta) o página viva sin resultados (el aviso se lleva
  puesta una foto de la página para que la próxima vez sepamos leerlo).
- **La vigilancia del keep-alive se mide antes del lote de búsquedas**, no
  después: cada búsqueda buena refresca su señal, así que preguntando al final un
  keep-alive averiado habría sido invisible para siempre.
- De propina: lo que escriba la página de Reuters ya no puede decidir si un fallo
  se reintenta (el texto entra citado), y el dato de «aguantó N días» sale ahora
  también por el login y la búsqueda, no solo por el keep-alive.

Una revisión adversarial (16 agentes en paralelo) sobre el arreglo cazó cuatro
flecos, cerrados antes de commitear:

- Los caminos manuales (botón ↻, backfill, alta de búsqueda) **solo anotan el
  fallo**: su éxito ya no rearma el disparador de la agencia — la variante
  manual de la misma tormenta.
- Una búsqueda que **revienta fuera del runner** (p. ej. la BD bloqueada) ya no
  para el lote ni desaparece del veredicto: cuenta como fallo de su agencia.
- Borrar una búsqueda con el lote en marcha («search not found») deja de contar
  como avería de la agencia: es administración, no una caída.
- La cita de marcas cubre el diagnóstico ENTERO (título y URL incluidos), no
  solo el texto del body: el `<title>` también lo escribe la página.

Y los papeles al día: MANUAL.md explica los tres avisos (y que el de la sesión
no se filtra por `alerts.agencies`), config.example.yaml lo mismo, y «Problemas
frecuentes» ya no receta re-login para cualquier error de Reuters. 173 pruebas
en verde, con dos ficheros nuevos de tests.

### Nace la bitácora del proyecto

BITACORA.md (el reenganche y las normas de la casa), REGISTRO.md (este fichero)
y CLAUDE.md (el enganche para las sesiones nuevas de Claude Code). Norma nueva:
los dos primeros se llevan al día en el mismo cambio que toque el programa.

### Los avisos admiten una postdata (19-08)

`alerts.postdata`: una coletilla que viaja al final de todos los avisos, con
instrucciones para quien los recibe. Y el manual explica cómo montar los avisos
con ntfy.sh, sin necesidad de webhook propio ni de crear cuenta.

---

## v1.2.1 — 2026-08-19

- Un fallo al abrir el navegador ya no deja el hilo envenenado, y cuando no hay
  sesión no se pierde el tiempo reintentando: no va a aparecer sola.

## v1.2.0 — 2026-08-18

La versión que sanea Reuters después del bloqueo de la cuenta.

- **El login de Reuters, a un clic y sin contraseña.** El programa no la teclea
  nunca: se abre el navegador normal, entras tú, y al cerrar la ventana la sesión
  se comprueba y se guarda sola.
- **El keep-alive mantiene la sesión viva** con una búsqueda real cada hora, que
  es lo que renueva el token de verdad.
- **Clasificador de estado de la sesión**: viva, muro de DataDome o caída. Manda
  el login sobre el muro, porque el login de Reuters llega con el captcha encima
  y confundirlos tapaba la única avería que exige un humano.
- El programa deja de necesitar una ventana de consola abierta: vive en el icono
  junto al reloj.
- Una búsqueda recién creada se llena sola, sin tener que pedir el histórico.

## v1.1.0 — 2026-08-09

- **AP arreglado**: el término de búsqueda va en `query` (`st` es el *tipo* de
  búsqueda), y sin `mediaType` la página encontraba las fotos pero no las pintaba.
- **macOS**: lanzador que sobrevive a Gatekeeper, con las instrucciones al día.
- Las fotos que aún no has visto salen destacadas en la rejilla.
- El nombre del fotógrafo lleva a su trabajo en la web de la agencia.
- El keep-alive deja señal fechada de que ha corrido, y alguien la vigila.
- El tropiezo aislado de una agencia ya no cuenta como ingesta fallida.
- Las búsquedas y el keep-alive corren en un hilo limpio (Playwright se niega a
  arrancar si hay un bucle de asyncio en el hilo).

## v1.0.0 — 2026-08-03

Primera versión pública.

- Panel con una luz de novedades por búsqueda, ordenable a mano y con
  separadores para agrupar; fotos a resolución nativa; un solo usuario.
- Las cuatro agencias funcionando: AP, Reuters, AFP y Getty.
- Paquete por sistema (Windows, macOS y Linux) compilado por GitHub Actions, sin
  que nadie tenga que instalar nada.
- El login de Reuters usa el navegador normal, en dos pasos, y la sesión se puede
  exportar e importar para llevarla a un servidor sin pantalla.
- Presentación en inglés y castellano.
