# vigilante-api

API operativa de Vigilante con timeline forense, manual reviews, case suggestions, lifecycle de casos, assignment/dashboard y primer slice funcional de auth/RBAC real.

## Qué implementa

- ingesta por fixture de eventos de `vigilante-recognition`;
- idempotencia por `event_id`;
- persistencia del envelope original en `api.timeline_event.payload.source_event`;
- timeline forense consultable;
- queues derivadas de manual reviews y case suggestions;
- resolución operativa de manual reviews, identity conflicts y case suggestions;
- promoción idempotente de case suggestions aceptadas a `api.case_record`;
- lifecycle básico de caso maestro: estado, cierre, reapertura y notas;
- assignment, filtros, paginación simple y dashboard summary;
- auth real contra `auth.app_user`;
- JWT stateless consumible por `vigilante-web`;
- endpoint de sesión actual `GET /api/v1/auth/me`;
- RBAC mínimo para `analyst` y `supervisor`;
- scope por organización y sitio usando `auth.user_organization_scope`;
- auditoría de acciones operativas ligada al usuario autenticado real.

## Decisión de diseño sobre BD real

La BD instalada sigue siendo la fuente de verdad. La app no ejecuta DDL ni migraciones en runtime.

Tablas usadas en este slice:

- `auth.app_user`: usuario, password hash, estado y metadata `username`;
- `auth.role` y `auth.user_role`: roles reales;
- `auth.user_organization_scope`: organizaciones permitidas y site scope opcional en `metadata.site_ids`;
- `api.organization` y `api.site`: contexto multiempresa;
- `api.timeline_event`: ancla persistente e idempotente del workflow;
- `api.case_record`, `api.case_item`, `api.case_note`, `api.case_status_history`: caso canónico, notas y lifecycle.

Las proyecciones de manual review y case suggestion siguen derivándose desde timeline porque los eventos de recognition llegan antes de que exista un caso maestro. Las acciones humanas escriben eventos auditables en timeline y, cuando la tabla real tiene columna UUID, también guardan `*_user_id`.

## Auth

### Estrategia

- Login con `POST /api/v1/auth/login`.
- Token JWT stateless firmado con HMAC-SHA256.
- Passwords con PBKDF2-SHA256, salt aleatorio e iteraciones configurables.
- Logout stateless: `POST /api/v1/auth/logout` valida el token y el cliente descarta el bearer token.
- En `APP_ENV=local|test`, si `AUTH_TOKEN_SECRET` no está definido se usa un secreto local de desarrollo. Fuera de local/test, `AUTH_TOKEN_SECRET` es obligatorio.

### Endpoints

- `POST /api/v1/auth/login`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/logout`

Login:

```json
{
  "username": "julio",
  "password": "demo123"
}
```

Respuesta:

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_at": "2026-04-26T18:00:00Z",
  "user": {
    "user_id": "00000000-0000-0000-0000-000000000101",
    "username": "julio",
    "email": "julio@example.test",
    "display_name": "Julio Analyst",
    "role": "analyst",
    "roles": ["analyst"],
    "is_active": true,
    "organization_ids": ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"],
    "site_ids": ["bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1"]
  },
  "roles": ["analyst"],
  "scope": {
    "organization_ids": ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"],
    "site_ids": ["bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1"]
  }
}
```

## RBAC mínimo

Roles soportados:

- `analyst`: lectura dentro de scope, notas, cambios de estado normales, autoasignación/desasignación mínima, resolución de manual reviews y case suggestions.
- `supervisor`: todo lo de analyst, reasignación, cierre, reapertura y promoción de suggestions a case.

Compatibilidad con roles existentes:

- `operator` y `reviewer` se tratan como `analyst`.
- `admin` se trata como `supervisor`.
- `auditor` puede leer dentro de scope, pero no operar.

## Scope organización/sitio

El scope se resuelve desde `auth.user_organization_scope`.

- `organization_id` define la organización accesible.
- `metadata.site_ids` limita sitios dentro de esa organización.
- Si `metadata.site_ids` no existe, el scope se interpreta como todos los sitios de la organización.
- Las lecturas sensibles filtran por scope.
- Las escrituras requieren scope operativo (`can_operate` o `can_admin`).
- Recursos fuera de scope devuelven `403`.

## Endpoints protegidos

Todos los endpoints bajo `/api/v1` requieren Bearer token excepto `/api/v1/auth/*`. `GET /health` queda público.

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
- `POST /api/v1/cases/{case_id}/assign`
- `POST /api/v1/cases/{case_id}/unassign`
- `POST /api/v1/cases/{case_id}/status`
- `POST /api/v1/cases/{case_id}/close`
- `POST /api/v1/cases/{case_id}/reopen`
- `GET /api/v1/cases/{case_id}/timeline`
- `GET /api/v1/cases/{case_id}/reviews`
- `GET /api/v1/cases/{case_id}/suggestions`
- `GET /api/v1/cases/{case_id}/notes`
- `POST /api/v1/cases/{case_id}/notes`

### Dashboard

- `GET /api/v1/dashboard/summary`

## Variables de entorno

Se puede usar `DB_URL` completo o `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD`.

```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=vigilante_api
DB_USER=julio
DB_PASSWORD=
DB_URL=
DB_SCHEMA_API=api
DB_SCHEMA_AUTH=auth
APP_NAME=vigilante-api
APP_ENV=local
LOG_LEVEL=INFO
DEFAULT_SOURCE_COMPONENT=vigilante-recognition
WORKFLOW_SOURCE_COMPONENT=vigilante-api
DEFAULT_QUERY_LIMIT=50
MAX_QUERY_LIMIT=200
AUTH_TOKEN_SECRET=change-me-local-dev-secret
AUTH_TOKEN_ISSUER=vigilante-api
AUTH_TOKEN_TTL_MINUTES=480
AUTH_PASSWORD_PBKDF2_ITERATIONS=260000
```

## Arranque local

Comandos de validación pedidos:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. pytest
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Seed demo opcional para la BD PostgreSQL local ya instalada:

```bash
PYTHONPATH=. DEMO_AUTH_PASSWORD=demo123 python3 scripts/seed_demo_auth.py
```

Usuarios demo locales:

- `julio` / `demo123`: `analyst`, scope org/site demo 1.
- `maria` / `demo123`: `supervisor`, scope org/site demo 1 y 2.

## Ejemplos rápidos

```bash
TOKEN=$(curl -s http://127.0.0.1:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"julio","password":"demo123"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/auth/me -H "authorization: Bearer $TOKEN"
curl http://127.0.0.1:8000/api/v1/manual-reviews -H "authorization: Bearer $TOKEN"
curl http://127.0.0.1:8000/api/v1/cases -H "authorization: Bearer $TOKEN"
curl http://127.0.0.1:8000/api/v1/dashboard/summary -H "authorization: Bearer $TOKEN"
```

Resolver una manual review. El backend ignora `resolved_by` del body para auditoría y usa el usuario autenticado:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/manual-reviews/<review_id>/resolve \
  -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"decision":"approved","decision_reason":"confirmed by analyst"}'
```

Promover una suggestion requiere supervisor:

```bash
SUPERVISOR_TOKEN=$(curl -s http://127.0.0.1:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"maria","password":"demo123"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl -X POST http://127.0.0.1:8000/api/v1/case-suggestions/<suggestion_id>/promote \
  -H "authorization: Bearer $SUPERVISOR_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"case_type":"unresolved_subject_case","title":"Recurring unidentified subject","priority":"medium","severity":"medium"}'
```

## Errores esperados

- `401 Missing bearer token`: endpoint protegido sin token.
- `401 Invalid username or password`: credenciales inválidas.
- `401 Invalid token signature/format/issuer/expiration`: token inválido.
- `403 User is inactive`: usuario inactivo.
- `403 Analyst role is required`: rol insuficiente para operación básica.
- `403 Supervisor role is required`: rol insuficiente para operación supervisora.
- `403 Resource is outside the authenticated user's scope`: recurso fuera de organización/sitio permitido.

## Pendientes para próximos slices auth/RBAC

- rotación e invalidación server-side de sesiones;
- gestión productiva de usuarios y permisos;
- cambio/recuperación de contraseña;
- SSO/OIDC empresarial;
- MFA;
- policy engine granular;
- permisos por site/zone/camera más finos;
- UI de administración.
