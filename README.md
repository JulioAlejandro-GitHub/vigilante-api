# vigilante-api

Slice 4 funcional de `vigilante-api` sobre la base de los Slice 1, 2 y 3.

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
- lifecycle básico del caso maestro;
- cambio de estado, cierre y reapertura de `case_record`;
- notas humanas mínimas sobre el caso;
- assignment y ownership textual básico del caso;
- filtros y paginación simple para casos, manual reviews y case suggestions;
- detalle enriquecido de caso para consumo de `vigilante-web`;
- resumen mínimo de dashboard/work queues;
- historial case-centric con acciones operativas, notas y relaciones;
- consultas de reviews, suggestions y eventos origen relacionados a un caso;
- auditoría de todas las decisiones, promociones y acciones de caso en timeline.

## Decisión de diseño sobre la BD real

La base instalada de `vigilante_api` trae `api.timeline_event`, `api.manual_review`, `api.manual_review_action`, `api.case_record`, `api.case_item`, `api.case_note` y `api.case_status_history`.

En este slice:

- `api.timeline_event` sigue siendo el ancla persistente e idempotente del workflow;
- las proyecciones de manual review y case suggestion se derivan colapsando:
  - evento base de recognition;
  - últimos eventos operativos de `vigilante-api`;
- `api.manual_review` no se usa como storage principal todavía porque exige `case_id`, y los reviews ingeridos desde recognition llegan antes del caso maestro;
- `api.case_record` sí se usa como tabla real y canónica al promover una suggestion aceptada;
- `api.case_note` se usa para notas humanas; el autor textual se audita en timeline porque el schema real solo trae `author_user_id`;
- `api.case_status_history` se usa para cambios de estado con valores compatibles con el check real;
- la asignación textual vive en `case_record.metadata.assignment` para no escribir UUIDs arbitrarios en `assigned_to_user_id` sin auth/users reales;
- `case_record.case_status` usa nombres válidos del baseline. La API expone `in_review`, `closed` y `reopened` como lifecycle lógico:
  - `in_review` se guarda como `under_review`;
  - `closed` se guarda como `resolved` más `closed_at` y metadata de lifecycle;
  - `reopened` se guarda como `open` más metadata de lifecycle.

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
- `case_status_changed`
- `case_note_added`
- `case_closed`
- `case_reopened`
- `case_assigned`
- `case_reassigned`
- `case_unassigned`

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

Filtros soportados en el listado: `status`, `review_type`, `priority`, `subject_id`, `camera_id`, `limit`, `offset`.

### Case suggestions

- `GET /api/v1/case-suggestions`
- `GET /api/v1/case-suggestions/{suggestion_id}`
- `POST /api/v1/case-suggestions/{suggestion_id}/resolve`
- `POST /api/v1/case-suggestions/{suggestion_id}/promote`

Filtros soportados en el listado: `status`, `suggestion_type`, `camera_id`, `subject_id`, `limit`, `offset`.

### Cases

- `GET /api/v1/cases`
- `GET /api/v1/cases/{case_id}`
- `POST /api/v1/cases/{case_id}/assign`
- `POST /api/v1/cases/{case_id}/unassign`
- `POST /api/v1/cases/{case_id}/status`
- `POST /api/v1/cases/{case_id}/close`
- `POST /api/v1/cases/{case_id}/reopen`
- `GET /api/v1/cases/{case_id}/timeline`
- `GET /api/v1/cases/{case_id}/reviews`
- `GET /api/v1/cases/{case_id}/suggestions`

Filtros soportados en el listado: `status`, `assigned_to`, `priority`, `severity`, `case_type`, `organization_id`, `site_id`, `q`, `limit`, `offset`.

Orden soportado: `sort_by=updated_at|opened_at|priority` y `sort_order=asc|desc`.

`GET /api/v1/cases/{case_id}` devuelve detalle enriquecido con datos base, assignment actual, notas recientes, reviews relacionadas, suggestions relacionadas y timeline relacionado.

### Case notes

- `GET /api/v1/cases/{case_id}/notes`
- `POST /api/v1/cases/{case_id}/notes`

### Dashboard

- `GET /api/v1/dashboard/summary`

Filtro opcional: `assigned_to`.

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

### Cambiar estado del caso

```json
{
  "status": "in_review",
  "reason": "analyst started evaluation",
  "changed_by": "julio"
}
```

### Cerrar caso

```json
{
  "reason": "sufficient evidence collected and handled",
  "changed_by": "julio"
}
```

### Reabrir caso

```json
{
  "reason": "new evidence arrived",
  "changed_by": "julio"
}
```

### Agregar nota al caso

```json
{
  "author": "julio",
  "note_text": "Analyst note about recurring unresolved subject"
}
```

### Asignar caso

```json
{
  "assigned_to": "julio",
  "assigned_by": "julio",
  "assignment_reason": "analyst taking ownership"
}
```

### Desasignar caso

```json
{
  "assigned_by": "julio",
  "assignment_reason": "returning to unassigned queue"
}
```

## Flujo mínimo del Slice 4

1. Ingerir evento de recognition por fixture.
2. Consultar `manual-reviews` o `case-suggestions`.
3. Resolver la entidad operativa por POST.
4. Si la suggestion queda aceptada, promoverla a `case_record`.
5. Cambiar estado, agregar notas, cerrar o reabrir el caso.
6. Asignar, reasignar o desasignar el caso.
7. Consultar `cases` con filtros, detalle enriquecido, timeline case-centric y dashboard summary para alimentar UI.

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
curl 'http://127.0.0.1:8000/api/v1/cases?status=in_review&assigned_to=julio&limit=25&offset=0&sort_by=updated_at'
curl http://127.0.0.1:8000/api/v1/cases/<case_id>
curl -X POST http://127.0.0.1:8000/api/v1/cases/<case_id>/assign \
  -H 'content-type: application/json' \
  -d '{"assigned_to":"julio","assigned_by":"julio","assignment_reason":"analyst taking ownership"}'
curl -X POST http://127.0.0.1:8000/api/v1/cases/<case_id>/unassign \
  -H 'content-type: application/json' \
  -d '{"assigned_by":"julio","assignment_reason":"returning to unassigned queue"}'
curl -X POST http://127.0.0.1:8000/api/v1/cases/<case_id>/status \
  -H 'content-type: application/json' \
  -d '{"status":"in_review","reason":"analyst started evaluation","changed_by":"julio"}'
curl -X POST http://127.0.0.1:8000/api/v1/cases/<case_id>/notes \
  -H 'content-type: application/json' \
  -d '{"author":"julio","note_text":"Analyst note about recurring unresolved subject"}'
curl -X POST http://127.0.0.1:8000/api/v1/cases/<case_id>/close \
  -H 'content-type: application/json' \
  -d '{"reason":"sufficient evidence collected and handled","changed_by":"julio"}'
curl -X POST http://127.0.0.1:8000/api/v1/cases/<case_id>/reopen \
  -H 'content-type: application/json' \
  -d '{"reason":"new evidence arrived","changed_by":"julio"}'
curl http://127.0.0.1:8000/api/v1/cases/<case_id>/notes
curl http://127.0.0.1:8000/api/v1/cases/<case_id>/timeline
curl http://127.0.0.1:8000/api/v1/cases/<case_id>/reviews
curl http://127.0.0.1:8000/api/v1/cases/<case_id>/suggestions
curl 'http://127.0.0.1:8000/api/v1/manual-reviews?status=pending&priority=3&limit=25&offset=0'
curl 'http://127.0.0.1:8000/api/v1/case-suggestions?status=pending&limit=25&offset=0'
curl 'http://127.0.0.1:8000/api/v1/dashboard/summary?assigned_to=julio'
curl http://127.0.0.1:8000/api/v1/timeline
```
