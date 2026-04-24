# vigilante-api

Primer slice funcional para consumir eventos ya emitidos por `vigilante-recognition` y materializar proyecciones operativas mínimas en `vigilante_api`.

## Qué implementa

- ingesta de eventos de recognition desde fixtures JSON;
- idempotencia por `event_id`;
- persistencia del envelope original del evento dentro de `api.timeline_event.payload.source_event`;
- timeline forense consultable;
- queue de manual reviews derivada y deduplicada desde eventos persistidos;
- queue de case suggestions derivada y deduplicada desde eventos persistidos;
- endpoints HTTP mínimos para consultar esas proyecciones.

## Decisión de diseño sobre la BD real

La base instalada de `vigilante_api` ya trae `api.timeline_event`, `api.manual_review` y `api.case_record`, pero:

- no trae `source_event_inbox`;
- no trae una proyección dedicada de `case_suggestion`;
- `api.manual_review` exige `case_id`, y este slice no debe crear todavía el caso maestro canónico final.

Por eso este slice usa `api.timeline_event` como ancla persistente e idempotente:

- `source_event_id` interno: `UUIDv5(source_component + event_id)` para adaptar el `event_id` string emitido por recognition al schema real `UUID`;
- `payload.source_event`: conserva el evento original completo para auditoría;
- `payload.manual_review_projection` y `payload.case_suggestion_projection`: guardan la forma canónica mínima consumida por los endpoints.

Esto deja el slice útil hoy sin meter DDL correctivo en runtime ni abrir casos automáticos todavía.

## Eventos soportados

- `human_presence_no_face`
- `face_detected_unidentified`
- `face_detected_identified`
- `manual_review_required`
- `identity_conflict`
- `recurrent_unresolved_subject`
- `case_suggestion_created`

## Endpoints

- `GET /health`
- `GET /api/v1/timeline`
- `GET /api/v1/timeline/{source_event_id}`
- `GET /api/v1/manual-reviews`
- `GET /api/v1/manual-reviews/{review_id}`
- `GET /api/v1/case-suggestions`
- `GET /api/v1/case-suggestions/{suggestion_id}`

## Variables de entorno

Se puede usar `DB_URL` completo o la matriz `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD`.

Defaults de runtime local:

- `DB_HOST=localhost`
- `DB_PORT=5432`
- `DB_NAME=vigilante_api`
- `DB_USER=julio`
- `DB_PASSWORD=`
- `DB_SCHEMA_API=api`

## Arranque local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. pytest
PYTHONPATH=. python3 -m app.ingest_fixture --fixture tests/fixtures/recognition_manual_review_required.json
PYTHONPATH=. python3 -m app.ingest_fixture --fixture tests/fixtures/recognition_case_suggestion_created.json
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Ejemplos rápidos

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/timeline
curl http://127.0.0.1:8000/api/v1/manual-reviews
curl http://127.0.0.1:8000/api/v1/case-suggestions
```
