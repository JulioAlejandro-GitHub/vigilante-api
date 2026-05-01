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
- enriquecimiento on-demand de evidencia contra `vigilante-media`;
- proyección oportunista live-first desde eventos recientes de `vigilante-recognition`;
- auditoría de acciones operativas ligada al usuario autenticado real.

## Decisión de diseño sobre BD real

La BD instalada sigue siendo la fuente de verdad. La app no ejecuta DDL ni migraciones en runtime.

Tablas usadas en este slice:

- `auth.app_user`: usuario, password hash, estado y metadata `username`;
- `auth.role` y `auth.user_role`: roles reales;
- `auth.user_organization_scope`: organizaciones permitidas y site scope opcional en `metadata.site_ids`;
- `api.organization` y `api.site`: contexto multiempresa;
- `api.camera`: catálogo de cámaras con configuración RTSP estructurada;
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

### Cameras

- `GET /api/v1/cameras`
- `GET /api/v1/cameras/{camera_id}`

Estos endpoints devuelven la configuración pública de cámara y excluyen siempre
`camera_secret`. Si metadata heredada contiene URLs RTSP con credenciales, la
respuesta las enmascara como `rtsp://user:***@host/...`; claves heredadas como
`camera_pass` o `camera_secret` se omiten.

## Evidencia media

Si `MEDIA_SERVICE_BASE_URL` está configurado, los endpoints operativos resuelven
on-demand referencias encontradas en `evidence_refs`, `frame_ref`, `frame_uri` y
claves equivalentes dentro del payload. La respuesta mantiene el payload original
y agrega `evidence_media` en timeline, manual reviews, case suggestions y casos.

Cada item de `evidence_media` incluye `ref`, `resolved`, `media_id`,
`content_type`, `content_url`, `thumbnail_url`, metadata del thumbnail
(`thumbnail_content_type`, `thumbnail_width`, `thumbnail_height`,
`thumbnail_available`, `thumbnail_status`), metadata del clip derivado
(`clip_available`, `clip_status`, `clip_url`, `clip_content_type`,
`clip_duration_seconds`, `clip_frame_count`, `clip_fps`, `clip_width`,
`clip_height`), `metadata_url`, dimensiones si están disponibles, timestamps y
metadata básica sanitizada. `content_url` apunta al original servido por
`vigilante-media`, por ejemplo
`http://localhost:8110/api/v1/media/{media_id}/content`; `thumbnail_url` apunta
al derivado liviano `.../{media_id}/thumbnail` cuando existe; `clip_url` apunta a
`.../{media_id}/clip/content` cuando `vigilante-media` puede construir un MP4
desde frames vecinos. No se exponen credenciales de MinIO/S3 ni URLs directas del
bucket.

Si `vigilante-media` no responde, devuelve error o no encuentra una referencia,
el recurso principal sigue respondiendo. El item queda con `resolved=false` y un
`error` manejable, y los `evidence_refs` originales permanecen en el payload.
Si una referencia resuelve sin thumbnail, `content_url` se conserva para que la
web pueda usarlo como fallback.
Si una referencia resuelve sin clip, `clip_available=false` y `clip_status`
explican el motivo sin afectar imagen, thumbnail ni respuesta principal.

### Lectura live-first vs fixtures

En `APP_ENV=local`, antes de leer `timeline`, `case-suggestions` y `cases`, la
API intenta proyectar eventos recientes desde `vigilante-recognition`
(`outbox.event_outbox` y, como fallback, `recognition.recognition_event`) hacia
`api.timeline_event`. La proyección solo incorpora eventos soportados que tengan
evidencia viva.

La heurística explícita es:

- fixture/demo: refs que empiezan con `tests/fixtures/`;
- live: refs que empiezan con `s3://` o `minio://`.

Cuando existe evidencia live, los listados ordenan primero los recursos con refs
live y relegan refs fixture. En `case-suggestions` y `cases`, los recursos
compuestos exclusivamente por fixtures se ocultan por defecto mientras exista
evidencia live en timeline. Si no hay evidencia live, los fixtures siguen
apareciendo como fallback de demo/test.

Se puede cambiar ese comportamiento local con:

- `LIVE_EVENT_PROJECTION_ENABLED=false` para desactivar la proyección desde
  recognition;
- `LIVE_PROJECTION_MAX_EVENTS=200` para ajustar cuántos eventos recientes se
  inspeccionan;
- `INCLUDE_FIXTURE_PROJECTIONS_WHEN_LIVE=true` para mantener fixtures visibles
  en suggestions/cases aun cuando haya evidencia live.

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
CAMERA_SECRET_FERNET_KEY=
MEDIA_SERVICE_BASE_URL=http://127.0.0.1:8110
MEDIA_SERVICE_PUBLIC_BASE_URL=http://127.0.0.1:8110
MEDIA_SERVICE_TIMEOUT_SECONDS=2
MEDIA_RESOLUTION_MAX_REFS=20
LIVE_EVENT_PROJECTION_ENABLED=true
LIVE_PROJECTION_MAX_EVENTS=200
INCLUDE_FIXTURE_PROJECTIONS_WHEN_LIVE=false
RECOGNITION_DB_URL=
RECOGNITION_DB_HOST=localhost
RECOGNITION_DB_PORT=5432
RECOGNITION_DB_NAME=vigilante_recognition
RECOGNITION_DB_USER=julio
RECOGNITION_DB_PASSWORD=
```

`CAMERA_SECRET_FERNET_KEY` debe ser la misma clave Fernet que usa
`vigilante-ingestion` para descifrar `api.camera.camera_secret` en runtime. Para
generar una clave local:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Secretos de camara

`camera_secret` se cifra desde la aplicación con Fernet y se guarda con prefijo
`fernet:v1:`. Ese prefijo distingue valores cifrados de secretos heredados en
texto plano. La escritura por ORM cifra automáticamente valores nuevos; valores
que ya empiezan con `fernet:v1:` se conservan.

Para migrar filas existentes y retirar secretos heredados de `metadata`:

```bash
PYTHONPATH=. python3 scripts/backfill_camera_secrets.py --dry-run
PYTHONPATH=. python3 scripts/backfill_camera_secrets.py
```

El backfill cifra `camera_secret` plano, puede promover temporalmente
`metadata.camera_pass` o la password de `metadata.stream_url` a
`camera_secret`, y deja `metadata.stream_url` enmascarado para que el valor real
no quede visible en la fila.

## Arranque local

Comandos de instalación y validación backend:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. pytest
PYTHONPATH=. python3 scripts/seed_demo_auth.py
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

El seed demo es solo para desarrollo local contra la BD PostgreSQL instalada. No ejecuta DDL ni migraciones: valida que existan las tablas/columnas de `api` y `auth`, usa el hasher real PBKDF2-SHA256 del backend y hace upserts idempotentes de organizaciones, sitios, roles, usuarios y scopes demo.

Se puede ejecutar repetidas veces:

```bash
PYTHONPATH=. python3 scripts/seed_demo_auth.py
PYTHONPATH=. python3 scripts/seed_demo_auth.py
```

Usuarios demo locales:

- `julio` / `demo123`: `analyst`, scope org/site demo 1.
- `maria` / `demo123`: `supervisor`, scope org/site demo 1 y 2.

El password demo por defecto es `demo123`. Para cambiarlo en un entorno local antes de sembrar:

```bash
DEMO_AUTH_PASSWORD=otra-clave PYTHONPATH=. python3 scripts/seed_demo_auth.py
```

El script se niega a correr si `APP_ENV` no es `local`, `dev`, `development`, `demo` o `test`, y también se niega a correr sobre SQLite. Está pensado para desbloquear validación local/demo, no para producción.

### Validación auth local

Con `uvicorn` corriendo en `127.0.0.1:8001`:

```bash
JULIO_TOKEN=$(curl -s http://127.0.0.1:8001/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"julio","password":"demo123"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl -s http://127.0.0.1:8001/api/v1/auth/me \
  -H "authorization: Bearer $JULIO_TOKEN"

MARIA_TOKEN=$(curl -s http://127.0.0.1:8001/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"maria","password":"demo123"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl -s http://127.0.0.1:8001/api/v1/auth/me \
  -H "authorization: Bearer $MARIA_TOKEN"

curl -i -s http://127.0.0.1:8001/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"julio","password":"wrong"}'

curl -s http://127.0.0.1:8001/api/v1/cases \
  -H "authorization: Bearer $JULIO_TOKEN"
```

Resultado esperado:

- login de `julio` devuelve `200`, `role: analyst`, org `aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1` y site `bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1`;
- login de `maria` devuelve `200`, `role: supervisor`, orgs demo 1 y 2, y sites demo 1 y 2;
- `/api/v1/auth/me` devuelve el usuario actual autenticado para ambos tokens;
- credenciales erróneas devuelven `401`;
- endpoints protegidos aceptan el bearer token válido.

### Validación con vigilante-web

En otra terminal:

```bash
cd ../vigilante-web
npm install
npm run dev
```

Abrir `http://127.0.0.1:5173/login` y autenticar con `julio / demo123` o `maria / demo123`.

En desarrollo, `vigilante-web` usa el proxy de Vite para reenviar `/api/*` a `VITE_API_BASE_URL` (`http://127.0.0.1:8001` por defecto). El flujo real usado por la web es:

- `POST /api/v1/auth/login`;
- persistencia local del JWT;
- `GET /api/v1/auth/me` para bootstrap de sesión y RBAC visual;
- requests protegidos con `Authorization: Bearer <token>`.

## Ejemplos rápidos

```bash
TOKEN=$(curl -s http://127.0.0.1:8001/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"julio","password":"demo123"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/api/v1/auth/me -H "authorization: Bearer $TOKEN"
curl http://127.0.0.1:8001/api/v1/manual-reviews -H "authorization: Bearer $TOKEN"
curl http://127.0.0.1:8001/api/v1/cases -H "authorization: Bearer $TOKEN"
curl http://127.0.0.1:8001/api/v1/dashboard/summary -H "authorization: Bearer $TOKEN"
```

Resolver una manual review. El backend ignora `resolved_by` del body para auditoría y usa el usuario autenticado:

```bash
curl -X POST http://127.0.0.1:8001/api/v1/manual-reviews/<review_id>/resolve \
  -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"decision":"approved","decision_reason":"confirmed by analyst"}'
```

Promover una suggestion requiere supervisor:

```bash
SUPERVISOR_TOKEN=$(curl -s http://127.0.0.1:8001/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"maria","password":"demo123"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl -X POST http://127.0.0.1:8001/api/v1/case-suggestions/<suggestion_id>/promote \
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
