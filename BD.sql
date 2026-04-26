CREATE TABLE auth.app_user (
	user_id uuid DEFAULT gen_random_uuid() NOT NULL,
	email public.citext NOT NULL,
	password_hash text NULL,
	display_name text NOT NULL,
	status text DEFAULT 'active'::text NOT NULL,
	is_active bool DEFAULT true NOT NULL,
	last_login_at timestamptz NULL,
	metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT app_user_email_key UNIQUE (email),
	CONSTRAINT app_user_pkey PRIMARY KEY (user_id),
	CONSTRAINT chk_app_user_status CHECK ((status = ANY (ARRAY['active'::text, 'inactive'::text, 'locked'::text, 'archived'::text])))
);
CREATE INDEX idx_auth_app_user_status ON auth.app_user USING btree (status, is_active);

-- Table Triggers

create trigger trg_auth_app_user_updated_at before
update
    on
    auth.app_user for each row execute function api.set_updated_at();
create trigger trg_audit_auth_app_user after
insert
    or
delete
    or
update
    on
    auth.app_user for each row execute function audit.log_row_change();

CREATE TABLE auth."permission" (
	permission_id uuid DEFAULT gen_random_uuid() NOT NULL,
	permission_key public.citext NOT NULL,
	description text NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT permission_permission_key_key UNIQUE (permission_key),
	CONSTRAINT permission_pkey PRIMARY KEY (permission_id)
);




CREATE TABLE auth."role" (
	role_id uuid DEFAULT gen_random_uuid() NOT NULL,
	role_key public.citext NOT NULL,
	display_name text NOT NULL,
	description text NULL,
	is_system bool DEFAULT false NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT role_pkey PRIMARY KEY (role_id),
	CONSTRAINT role_role_key_key UNIQUE (role_key)
);

-- Table Triggers

create trigger trg_auth_role_updated_at before
update
    on
    auth.role for each row execute function api.set_updated_at();

CREATE TABLE auth.role_permission (
	role_permission_id uuid DEFAULT gen_random_uuid() NOT NULL,
	role_id uuid NOT NULL,
	permission_id uuid NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT role_permission_pkey PRIMARY KEY (role_permission_id),
	CONSTRAINT role_permission_role_id_permission_id_key UNIQUE (role_id, permission_id),
	CONSTRAINT role_permission_permission_id_fkey FOREIGN KEY (permission_id) REFERENCES auth."permission"(permission_id) ON DELETE CASCADE,
	CONSTRAINT role_permission_role_id_fkey FOREIGN KEY (role_id) REFERENCES auth."role"(role_id) ON DELETE CASCADE
);


CREATE TABLE auth.user_organization_scope (
	user_organization_scope_id uuid DEFAULT gen_random_uuid() NOT NULL,
	user_id uuid NOT NULL,
	organization_id uuid NOT NULL,
	scope_role text NOT NULL,
	can_view bool DEFAULT true NOT NULL,
	can_operate bool DEFAULT false NOT NULL,
	can_admin bool DEFAULT false NOT NULL,
	metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT chk_auth_user_org_scope_role CHECK ((scope_role = ANY (ARRAY['viewer'::text, 'operator'::text, 'reviewer'::text, 'admin'::text, 'auditor'::text, 'integrator'::text]))),
	CONSTRAINT user_organization_scope_pkey PRIMARY KEY (user_organization_scope_id),
	CONSTRAINT user_organization_scope_user_id_organization_id_key UNIQUE (user_id, organization_id),
	CONSTRAINT user_organization_scope_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES api.organization(organization_id) ON DELETE CASCADE,
	CONSTRAINT user_organization_scope_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.app_user(user_id) ON DELETE CASCADE
);
CREATE INDEX idx_auth_user_org_scope_user ON auth.user_organization_scope USING btree (user_id, organization_id);

-- Table Triggers

create trigger trg_audit_auth_user_organization_scope after
insert
    or
delete
    or
update
    on
    auth.user_organization_scope for each row execute function audit.log_row_change();


CREATE TABLE auth.user_role (
	user_role_id uuid DEFAULT gen_random_uuid() NOT NULL,
	user_id uuid NOT NULL,
	role_id uuid NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT user_role_pkey PRIMARY KEY (user_role_id),
	CONSTRAINT user_role_user_id_role_id_key UNIQUE (user_id, role_id),
	CONSTRAINT user_role_role_id_fkey FOREIGN KEY (role_id) REFERENCES auth."role"(role_id) ON DELETE CASCADE,
	CONSTRAINT user_role_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.app_user(user_id) ON DELETE CASCADE
);