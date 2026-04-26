-- add_auth_user_from_template_actual_schema.sql
-- Crea o actualiza un usuario real en vigilante_api usando el esquema auth real.
--
-- Uso:
--   psql -U julio -d vigilante_api -f add_auth_user_from_template_actual_schema.sql
--
-- Idea:
--   - toma un usuario template que YA pueda loguear (por ejemplo julio o maria)
--   - clona su password_hash
--   - clona sus roles en auth.user_role
--   - clona su scope en auth.user_organization_scope
--   - crea/actualiza el nuevo usuario con display_name + metadata.username
--
-- Resultado:
--   el nuevo usuario podrá iniciar sesión con la MISMA contraseña del template
--   (por ejemplo demo123 si el template es un usuario demo sembrado).
--
-- Parámetros a editar dentro del bloque DO:
--   v_new_user_id
--   v_new_username
--   v_new_email
--   v_new_display_name
--   v_template_username
--
-- Opcional:
--   si quieres copiar desde un supervisor, usa v_template_username := 'maria'
--   si quieres copiar desde un analyst, usa v_template_username := 'julio'

BEGIN;

SET search_path TO public, auth, api;

DO $$
DECLARE
    -- =========================================================
    -- EDITA SOLO ESTOS VALORES
    -- =========================================================
    v_new_user_id       uuid := '00000000-0000-0000-0000-000000000201';
    v_new_username      text := 'nuevo_analista';
    v_new_email         text := 'nuevo_analista@local.test';
    v_new_display_name  text := 'Nuevo Analista';
    v_template_username text := 'julio';
    -- =========================================================

    v_template_user_id uuid;
    v_template_password_hash text;
    v_template_metadata jsonb;
BEGIN
    -- Validaciones mínimas de esquema real
    IF to_regclass('auth.app_user') IS NULL THEN
        RAISE EXCEPTION 'No existe auth.app_user';
    END IF;

    IF to_regclass('auth.user_role') IS NULL THEN
        RAISE EXCEPTION 'No existe auth.user_role';
    END IF;

    IF to_regclass('auth.user_organization_scope') IS NULL THEN
        RAISE EXCEPTION 'No existe auth.user_organization_scope';
    END IF;

    IF to_regclass('auth.role') IS NULL THEN
        RAISE EXCEPTION 'No existe auth.role';
    END IF;

    -- Resolver usuario template desde username(metadata), email o user_id
    SELECT
        u.user_id,
        u.password_hash,
        COALESCE(u.metadata, '{}'::jsonb)
    INTO
        v_template_user_id,
        v_template_password_hash,
        v_template_metadata
    FROM auth.app_user u
    WHERE u.user_id::text = v_template_username
       OR lower(u.email::text) = lower(v_template_username)
       OR lower(COALESCE(u.metadata->>'username', '')) = lower(v_template_username)
    LIMIT 1;

    IF v_template_user_id IS NULL THEN
        RAISE EXCEPTION
            'No se encontró el usuario template "%". Ejecuta primero el seed demo o usa otro template.',
            v_template_username;
    END IF;

    IF v_template_password_hash IS NULL OR length(v_template_password_hash) = 0 THEN
        RAISE EXCEPTION
            'El usuario template "%" no tiene password_hash válido.',
            v_template_username;
    END IF;

    -- Crear o actualizar app_user
    INSERT INTO auth.app_user (
        user_id,
        email,
        password_hash,
        display_name,
        status,
        is_active,
        metadata
    )
    VALUES (
        v_new_user_id,
        v_new_email,
        v_template_password_hash,
        v_new_display_name,
        'active',
        TRUE,
        jsonb_strip_nulls(
            v_template_metadata
            || jsonb_build_object(
                'username', v_new_username,
                'display_name', v_new_display_name,
                'full_name', v_new_display_name,
                'seed_source', 'add_auth_user_from_template_actual_schema.sql',
                'auth_template_username', v_template_username
            )
        )
    )
    ON CONFLICT (user_id) DO UPDATE
    SET
        email = EXCLUDED.email,
        password_hash = EXCLUDED.password_hash,
        display_name = EXCLUDED.display_name,
        status = EXCLUDED.status,
        is_active = EXCLUDED.is_active,
        metadata = EXCLUDED.metadata;

    -- Aviso lógico por username duplicado en metadata (no siempre hay constraint)
    IF EXISTS (
        SELECT 1
        FROM auth.app_user u
        WHERE lower(COALESCE(u.metadata->>'username', '')) = lower(v_new_username)
          AND u.user_id <> v_new_user_id
    ) THEN
        RAISE WARNING 'Ya existe otro usuario con metadata.username = %', v_new_username;
    END IF;

    -- Aviso lógico por email duplicado con user_id distinto
    IF EXISTS (
        SELECT 1
        FROM auth.app_user u
        WHERE lower(u.email::text) = lower(v_new_email)
          AND u.user_id <> v_new_user_id
    ) THEN
        RAISE WARNING 'Ya existe otro usuario con email = %', v_new_email;
    END IF;

    -- Clonar roles del template al nuevo usuario
    INSERT INTO auth.user_role (
        user_id,
        role_id
    )
    SELECT
        v_new_user_id,
        ur.role_id
    FROM auth.user_role ur
    WHERE ur.user_id = v_template_user_id
    ON CONFLICT (user_id, role_id) DO NOTHING;

    -- Clonar scope multiempresa/sitio del template
    INSERT INTO auth.user_organization_scope (
        user_id,
        organization_id,
        scope_role,
        can_view,
        can_operate,
        can_admin,
        metadata
    )
    SELECT
        v_new_user_id,
        s.organization_id,
        s.scope_role,
        s.can_view,
        s.can_operate,
        s.can_admin,
        COALESCE(s.metadata, '{}'::jsonb)
    FROM auth.user_organization_scope s
    WHERE s.user_id = v_template_user_id
    ON CONFLICT (user_id, organization_id) DO UPDATE
    SET
        scope_role = EXCLUDED.scope_role,
        can_view = EXCLUDED.can_view,
        can_operate = EXCLUDED.can_operate,
        can_admin = EXCLUDED.can_admin,
        metadata = EXCLUDED.metadata;

    RAISE NOTICE 'Usuario % creado/actualizado correctamente.', v_new_username;
    RAISE NOTICE 'Template usado: %', v_template_username;
    RAISE NOTICE 'El nuevo usuario entra con la MISMA contraseña del template.';
END
$$;

COMMIT;

-- ============================================================
-- VALIDACIONES SUGERIDAS
-- ============================================================
-- 1) Usuario:
-- SELECT
--   user_id,
--   email,
--   display_name,
--   status,
--   is_active,
--   metadata->>'username' AS username
-- FROM auth.app_user
-- WHERE user_id = '00000000-0000-0000-0000-000000000201'::uuid;

-- 2) Roles:
-- SELECT
--   ur.user_id,
--   r.role_key,
--   r.display_name
-- FROM auth.user_role ur
-- JOIN auth.role r ON r.role_id = ur.role_id
-- WHERE ur.user_id = '00000000-0000-0000-0000-000000000201'::uuid
-- ORDER BY r.role_key;

-- 3) Scope:
-- SELECT
--   user_id,
--   organization_id,
--   scope_role,
--   can_view,
--   can_operate,
--   can_admin,
--   metadata
-- FROM auth.user_organization_scope
-- WHERE user_id = '00000000-0000-0000-0000-000000000201'::uuid
-- ORDER BY organization_id;

-- 4) Login esperado:
-- Si v_template_username = 'julio' o 'maria' y el seed demo está aplicado,
-- entonces el nuevo usuario entra con la misma contraseña del template
-- (por ejemplo demo123).
--
-- curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
--   -H "Content-Type: application/json" \
--   -d '{"username":"nuevo_analista","password":"demo123"}'
