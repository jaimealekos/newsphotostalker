# Contribuir

¡Gracias por el interés! Este es un proyecto pequeño y las mejoras son bienvenidas.

## Puesta en marcha para desarrollo

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.yaml config.local.yaml   # arranca en modo mock por defecto
python -m scripts.seed             # siembra las búsquedas de ejemplo
uvicorn app.main:app --reload
```

## Antes de abrir un PR

- Pasa los tests: `python -m pytest -q`.
- Mantén el estilo del código de alrededor (nombres, comentarios concisos).
- Si tocas un adaptador de agencia, describe qué verificaste contra el servicio real.

## Aviso sobre scraping

Los adaptadores en vivo dependen del maquetado y las APIs de terceros (AP, Reuters,
Getty/AFP), que **cambian sin avisar**. Si un adaptador se rompe, abre un issue con
el error y, si puedes, el fragmento de HTML/JSON que cambió.

Usa la herramienta de forma responsable y respetando los Términos de Servicio de
cada agencia y tu propia cuenta (Reuters requiere una sesión con login propia).
