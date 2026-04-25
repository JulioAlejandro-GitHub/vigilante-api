# vigilante-api

Slice 2 funcional de `vigilante-api` sobre la base del Slice 1.

## Qué implementa

- ingesta por fixture de eventos de `vigilante-recognition`;
- idempotencia por `event_id`;
- persistencia del envelope original del evento dentro de `api.timeline_event.payload.source_event`;
- timeline forense consultable;
- queue derivada de manual reviews;
- queue derivada de case suggestions;
- resolución operativa de manual reviews;
- resolución operativa de identity conflicts;
- resolución operativa de case suggestions;
- promoción idempotente de case suggestions aceptadas a `api.case_record`;
- auditoría de todas las decisiones y promociones en timeline.

## Decisión de diseño sobre la BD real

La base instalada de `vigilante_api` trae `api.timeline_event`, `api.manual_review`, `api.manual_review_action`, `api.case_record` y `api.case_item`.

En este slice:

- `api.timeline_event` sigue siendo el ancla persistente e idempotente del workflow;
- las proyecciones de manual review y case suggestion se derivan colapsando:
  - evento base de recognition;
  - últimos eventos operativos de `vigilante-api`;
- `api.manual_review` no se usa como storage principal todavía porque exige `case_id`, y los reviews ingeridos desde recognition llegan antes del caso maestro;
- `api.case_record` sí se usa como tabla real y canónica al promover una suggestion aceptada.

## Eventos de entrada soportados

- `human_presence_no_face`
- `face_detected_unidentified`
- `face_detected_identified`
- `manual_review_required`
- `identity_conflict`
- `recurrent_unresolved_subject`
- `case_suggestion_created`

## Eventos operativos internos emitidos a timeline

- `manual_review_resolved`
- `identity_conflict_resolved`
- `case_suggestion_resolved`
- `case_record_created`

## Endpoints

### Health

- `GET /health`

### Timeline

- `GET /api/v1/timeline`
- `GET /api/v1/timeline/{source_event_id}`

### Manual reviews

- `GET /api/v1/manual-reviews`
- `GET /api/v1/manual-reviews/{review_id}`
- `POST /api/v1/manual-reviews/{review_id}/resolve`

### Case suggestions

- `GET /api/v1/case-suggestions`
- `GET /api/v1/case-suggestions/{suggestion_id}`
- `POST /api/v1/case-suggestions/{suggestion_id}/resolve`
- `POST /api/v1/case-suggestions/{suggestion_id}/promote`

### Cases

- `GET /api/v1/cases`
- `GET /api/v1/cases/{case_id}`

## Variables de entorno

Se puede usar `DB_URL` completo o la matriz `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD`.

Defaults de runtime local:

- `DB_HOST=localhost`
- `DB_PORT=5432`
- `DB_NAME=vigilante_api`
- `DB_USER=julio`
- `DB_PASSWORD=`
- `DB_SCHEMA_API=api`
- `DEFAULT_SOURCE_COMPONENT=vigilante-recognition`
- `WORKFLOW_SOURCE_COMPONENT=vigilante-api`

## Arranque local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. pytest
PYTHONPATH=. python3 -m app.ingest_fixture --fixture tests/fixtures/recognition_manual_review_required.json
PYTHONPATH=. python3 -m app.ingest_fixture --fixture tests/fixtures/recognition_identity_conflict.json
PYTHONPATH=. python3 -m app.ingest_fixture --fixture tests/fixtures/recognition_case_suggestion_created.json
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Payloads mínimos

### Resolver manual review

```json
{
  "decision": "approved",
  "decision_reason": "confirmed by analyst",
  "resolved_by": "julio"
}
```

### Resolver identity conflict

```json
{
  "decision": "approved",
  "decision_reason": "confirmed by analyst after continuity review",
  "resolved_by": "julio",
  "identity_resolution": "confirm_identity",
  "confirmed_person_profile_id": "44444444-4444-4444-4444-444444444441"
}
```

### Resolver case suggestion

```json
{
  "decision": "accepted",
  "decision_reason": "sufficient evidence for case creation",
  "resolved_by": "julio"
}
```

### Promover case suggestion a case_record

```json
{
  "resolved_by": "julio",
  "case_type": "unresolved_subject_case",
  "title": "Recurring unidentified subject",
  "priority": "medium",
  "severity": "medium"
}
```

Nota: el schema real de `api.case_record.case_type` no acepta `unresolved_subject_case`; el servicio lo mapea a `multi_event_tracking` y preserva el valor pedido en metadata.

## Flujo mínimo del Slice 2

1. Ingerir evento de recognition por fixture.
2. Consultar `manual-reviews` o `case-suggestions`.
3. Resolver la entidad operativa por POST.
4. Si la suggestion queda aceptada, promoverla a `case_record`.
5. Consultar `cases` y `timeline` para auditar el resultado.

## Ejemplos rápidos

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/manual-reviews
curl http://127.0.0.1:8000/api/v1/case-suggestions
curl -X POST http://127.0.0.1:8000/api/v1/case-suggestions/<suggestion_id>/resolve \
  -H 'content-type: application/json' \
  -d '{"decision":"accepted","decision_reason":"sufficient evidence for case creation","resolved_by":"julio"}'
curl -X POST http://127.0.0.1:8000/api/v1/case-suggestions/<suggestion_id>/promote \
  -H 'content-type: application/json' \
  -d '{"resolved_by":"julio","case_type":"unresolved_subject_case","title":"Recurring unidentified subject","priority":"medium","severity":"medium"}'
curl http://127.0.0.1:8000/api/v1/cases
curl http://127.0.0.1:8000/api/v1/timeline
```
