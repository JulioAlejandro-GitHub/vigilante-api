-- add_api_camera_flexible.sql
-- Script flexible para insertar o actualizar una cámara en vigilante_api.api.camera
--
-- Uso:
--   psql -U julio -d vigilante_api -f add_api_camera_flexible.sql
--
-- Qué hace:
--   - valida que exista api.camera
--   - arma un INSERT/UPSERT dinámico usando solo columnas que realmente existan
--   - soporta las columnas más probables del modelo Vigilante:
--       camera_id, organization_id, site_id, external_camera_key,
--       display_name / camera_name / name,
--       source_type, stream_url, status, is_active, metadata
--
-- Nota importante:
--   como no tengo el DDL exacto de api.camera en este mensaje,
--   el script es "best effort". Si tu tabla tiene otras columnas NOT NULL sin default,
--   PostgreSQL te lo dirá al ejecutar. En ese caso pásame el:
--       \d api.camera
--   y te lo dejo exacto.

BEGIN;

SET search_path TO public, api, auth;

DO $$
DECLARE
    -- =========================================================
    -- EDITA SOLO ESTOS VALORES
    -- =========================================================
    v_camera_id            uuid  := '11111111-1111-1111-1111-111111111111';
    v_organization_id      uuid  := 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1';
    v_site_id              uuid  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1';
    v_external_camera_key  text  := 'cam_101';
    v_display_name         text  := 'Camara Acceso Principal';
    v_source_type          text  := 'file_replay';
    v_stream_url           text  := 'samples/cam01.mp4';
    v_status               text  := 'active';
    v_is_active            boolean := TRUE;
    v_metadata             jsonb := jsonb_build_object(
        'seed_source', 'add_api_camera_flexible.sql',
        'notes', 'camera creada para pruebas locales'
    );
    -- =========================================================

    v_cols text[] := ARRAY[]::text[];
    v_vals text[] := ARRAY[]::text[];
    v_updates text[] := ARRAY[]::text[];
    v_sql text;
    v_name_col text;
BEGIN
    IF to_regclass('api.camera') IS NULL THEN
        RAISE EXCEPTION 'No existe api.camera';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'api' AND table_name = 'camera' AND column_name = 'camera_id'
    ) THEN
        v_cols := array_append(v_cols, 'camera_id');
        v_vals := array_append(v_vals, quote_literal(v_camera_id::text) || '::uuid');
        v_updates := array_append(v_updates, 'camera_id = EXCLUDED.camera_id');
    ELSE
        RAISE EXCEPTION 'api.camera no tiene columna camera_id';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'api' AND table_name = 'camera' AND column_name = 'organization_id'
    ) THEN
        IF v_organization_id IS NULL THEN
            RAISE EXCEPTION 'organization_id es requerido por esta tabla y no se definió';
        END IF;
        v_cols := array_append(v_cols, 'organization_id');
        v_vals := array_append(v_vals, quote_literal(v_organization_id::text) || '::uuid');
        v_updates := array_append(v_updates, 'organization_id = EXCLUDED.organization_id');
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'api' AND table_name = 'camera' AND column_name = 'site_id'
    ) THEN
        IF v_site_id IS NULL THEN
            RAISE EXCEPTION 'site_id es requerido por esta tabla y no se definió';
        END IF;
        v_cols := array_append(v_cols, 'site_id');
        v_vals := array_append(v_vals, quote_literal(v_site_id::text) || '::uuid');
        v_updates := array_append(v_updates, 'site_id = EXCLUDED.site_id');
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'api' AND table_name = 'camera' AND column_name = 'external_camera_key'
    ) THEN
        v_cols := array_append(v_cols, 'external_camera_key');
        v_vals := array_append(v_vals, quote_literal(v_external_camera_key));
        v_updates := array_append(v_updates, 'external_camera_key = EXCLUDED.external_camera_key');
    END IF;

    SELECT c.column_name
    INTO v_name_col
    FROM information_schema.columns c
    WHERE c.table_schema = 'api'
      AND c.table_name = 'camera'
      AND c.column_name IN ('display_name', 'camera_name', 'name')
    ORDER BY CASE c.column_name
        WHEN 'display_name' THEN 1
        WHEN 'camera_name' THEN 2
        WHEN 'name' THEN 3
        ELSE 99
    END
    LIMIT 1;

    IF v_name_col IS NOT NULL THEN
        v_cols := array_append(v_cols, v_name_col);
        v_vals := array_append(v_vals, quote_literal(v_display_name));
        v_updates := array_append(v_updates, format('%I = EXCLUDED.%I', v_name_col, v_name_col));
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'api' AND table_name = 'camera' AND column_name = 'source_type'
    ) THEN
        v_cols := array_append(v_cols, 'source_type');
        v_vals := array_append(v_vals, quote_literal(v_source_type));
        v_updates := array_append(v_updates, 'source_type = EXCLUDED.source_type');
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'api' AND table_name = 'camera' AND column_name = 'stream_url'
    ) THEN
        v_cols := array_append(v_cols, 'stream_url');
        v_vals := array_append(v_vals, quote_literal(v_stream_url));
        v_updates := array_append(v_updates, 'stream_url = EXCLUDED.stream_url');
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'api' AND table_name = 'camera' AND column_name = 'status'
    ) THEN
        v_cols := array_append(v_cols, 'status');
        v_vals := array_append(v_vals, quote_literal(v_status));
        v_updates := array_append(v_updates, 'status = EXCLUDED.status');
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'api' AND table_name = 'camera' AND column_name = 'is_active'
    ) THEN
        v_cols := array_append(v_cols, 'is_active');
        v_vals := array_append(v_vals, CASE WHEN v_is_active THEN 'TRUE' ELSE 'FALSE' END);
        v_updates := array_append(v_updates, 'is_active = EXCLUDED.is_active');
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'api' AND table_name = 'camera' AND column_name = 'metadata'
    ) THEN
        v_cols := array_append(v_cols, 'metadata');
        v_vals := array_append(v_vals, quote_literal(v_metadata::text) || '::jsonb');
        v_updates := array_append(v_updates, 'metadata = EXCLUDED.metadata');
    END IF;

    v_sql := format(
        'INSERT INTO api.camera (%s) VALUES (%s) ON CONFLICT (camera_id) DO UPDATE SET %s',
        array_to_string(ARRAY(SELECT format('%I', x) FROM unnest(v_cols) AS x), ', '),
        array_to_string(v_vals, ', '),
        array_to_string(v_updates, ', ')
    );

    RAISE NOTICE 'Ejecutando: %', v_sql;
    EXECUTE v_sql;

    RAISE NOTICE 'Cámara insertada/actualizada correctamente: %', v_camera_id;
END
$$;

COMMIT;

-- ============================================================
-- VALIDACIONES SUGERIDAS
-- ============================================================
-- 1) Ver estructura real:
--    \d api.camera
--
-- 2) Ver la cámara:
-- SELECT *
-- FROM api.camera
-- WHERE camera_id = '11111111-1111-1111-1111-111111111111'::uuid;
