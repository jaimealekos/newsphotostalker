"""Enlaces a la web de cada agencia, para ir a ver allí lo mismo que ve la app.

El panel muestra el nombre del fotógrafo en cada foto. Poder pulsarlo y caer en
la búsqueda de ese fotógrafo **en la agencia** ahorra el paseo de siempre: abrir
la web, encontrar el buscador y volver a teclear el nombre.

Las URL no se inventan aquí: son las mismas que usan los adaptadores para
buscar, importadas de ellos, así que si una agencia cambia su formato solo hay
un sitio que tocar. La única propia es la de AP, porque su adaptador habla con
una API y no con la web (misma consulta, eso sí: ``photographer.name``).
"""

from __future__ import annotations

from urllib.parse import quote, quote_plus

from .ingest.getty import SEARCH_BASE as GETTY_BASE
from .ingest.reuters import SEARCH_URL as REUTERS_BUSQUEDA

#: AP Newsroom. El término de búsqueda va en ``query``.
#:
#: **``st`` NO es el término**, aunque lo parezca: es el TIPO de búsqueda. Se
#: leyó en el propio código de su web, que hace
#: ``this.query = p.get("query"), this.searchType = p.get("st")``. Mandar el
#: nombre por ``st`` deja la página en blanco, que es exactamente lo que pasaba.
#: Lo confirma la propia AP por otro lado: ``apimages.com/Search?query=<nombre>``
#: redirige a ``newsroom.ap.org/editorial-photos-videos/…?query=<nombre>``.
#:
#: Va el nombre tal cual y no ``photographer.name:"…"``: ese lenguaje de campos
#: lo entiende su API (comprobado: un campo inventado devuelve 0 filas y
#: ``photographer.name`` devuelve 28.770), pero su buscador web no lo aplicó.
#: A cambio la búsqueda es por texto y trae también fotos donde a esa persona la
#: mencionan sin ser suyas: unas 80 sobre 28.770, un 0,3 %.
AP_BUSQUEDA = "https://newsroom.ap.org/editorial-photos-videos/search?query={q}"


def url_del_fotografo(agency: str, nombre: str | None) -> str | None:
    """Búsqueda de ese fotógrafo en la web de esa agencia, o None si no aplica.

    Devuelve None —y entonces el panel deja el nombre sin enlazar— cuando no hay
    nombre o la agencia no es una de las cuatro. Nunca lanza: es un adorno de la
    interfaz y no debe poder tumbar una página.
    """
    nombre = (nombre or "").strip()
    if not nombre or not agency:
        return None

    agencia = agency.strip().lower()
    if agencia == "reuters":
        return REUTERS_BUSQUEDA.format(q=quote(nombre))
    if agencia in ("getty", "afp"):
        # Mismos parámetros que build_search_url del adaptador: autoría exacta,
        # editorial, lo más nuevo primero. AFP se distribuye por Getty, y se
        # acota a su colección.
        partes = [
            "family=editorial",
            "sort=newest",
            "assettype=image",
            f"artistexact={quote_plus(nombre)}",
        ]
        if agencia == "afp":
            partes.append("collections=afp")
        return f"{GETTY_BASE}?{'&'.join(partes)}"
    if agencia == "ap":
        return AP_BUSQUEDA.format(q=quote(nombre))
    return None
