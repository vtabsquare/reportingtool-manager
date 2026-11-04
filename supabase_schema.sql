-- ==============================================================================
-- VTAB REPORTING MANAGER - SUPABASE (POSTGRESQL) PRODUCTION SCHEMA & MIGRATION
-- ==============================================================================
-- Target Database: Supabase PostgreSQL (Project: asjscibujiikthxmpxgg)
-- Purpose: Control plane database for VTAB Reporting Manager
-- Safe to run in Supabase SQL Editor: Creates missing tables & adds new columns
-- ==============================================================================

-- 1. CLIENTS TABLE
CREATE TABLE IF NOT EXISTS public.clients (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    contact_email TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    environment TEXT NOT NULL DEFAULT 'production',
    services_url TEXT,
    admin_url TEXT,
    database_provider TEXT NOT NULL DEFAULT 'supabase',
    installed_services_version TEXT,
    installed_admin_version TEXT,
    last_health_status TEXT NOT NULL DEFAULT 'unknown',
    last_health_at TEXT,
    client_admin_token_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS contact_email TEXT;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS client_admin_token_hash TEXT;

-- 2. CLIENT AGENTS TABLE
CREATE TABLE IF NOT EXISTS public.client_agents (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'registered',
    version TEXT,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    last_seen_at TEXT,
    created_at TEXT NOT NULL
);

-- 3. RELEASES TABLE
CREATE TABLE IF NOT EXISTS public.releases (
    id TEXT PRIMARY KEY,
    component TEXT NOT NULL,
    version TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'stable',
    status TEXT NOT NULL DEFAULT 'draft',
    artifact_url TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    min_connector_version TEXT,
    release_notes TEXT NOT NULL DEFAULT '',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    git_commit_sha TEXT,
    repository_url TEXT,
    migration_index INTEGER NOT NULL DEFAULT 0,
    validation_status TEXT NOT NULL DEFAULT 'pending',
    validation_details_json TEXT NOT NULL DEFAULT '{}',
    validated_by TEXT,
    validated_at TEXT,
    source_event_id TEXT,
    superseded_by_release_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(component, version, channel)
);
ALTER TABLE public.releases ADD COLUMN IF NOT EXISTS git_commit_sha TEXT;
ALTER TABLE public.releases ADD COLUMN IF NOT EXISTS repository_url TEXT;
ALTER TABLE public.releases ADD COLUMN IF NOT EXISTS migration_index INTEGER NOT NULL DEFAULT 0;
ALTER TABLE public.releases ADD COLUMN IF NOT EXISTS validation_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE public.releases ADD COLUMN IF NOT EXISTS validation_details_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE public.releases ADD COLUMN IF NOT EXISTS validated_by TEXT;
ALTER TABLE public.releases ADD COLUMN IF NOT EXISTS validated_at TEXT;
ALTER TABLE public.releases ADD COLUMN IF NOT EXISTS source_event_id TEXT;
ALTER TABLE public.releases ADD COLUMN IF NOT EXISTS superseded_by_release_id TEXT;

-- 4. DEPLOYMENTS TABLE
CREATE TABLE IF NOT EXISTS public.deployments (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
    release_id TEXT NOT NULL REFERENCES public.releases(id),
    status TEXT NOT NULL DEFAULT 'pending_approval',
    approved_by TEXT,
    scheduled_for TEXT,
    started_at TEXT,
    finished_at TEXT,
    previous_version TEXT,
    target_version TEXT NOT NULL,
    error_message TEXT,
    rollback_available BOOLEAN NOT NULL DEFAULT TRUE,
    deployment_type TEXT NOT NULL DEFAULT 'update',
    provider TEXT NOT NULL DEFAULT 'agent',
    stage TEXT NOT NULL DEFAULT 'pending_approval',
    progress_percent INTEGER NOT NULL DEFAULT 0,
    rollback_snapshot_ref TEXT,
    agent_notes TEXT,
    idempotency_key TEXT,
    provider_deploy_id TEXT,
    installed_version TEXT,
    installed_commit_sha TEXT,
    health_verified_at TEXT,
    cancelled_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS deployment_type TEXT NOT NULL DEFAULT 'update';
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'agent';
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS stage TEXT NOT NULL DEFAULT 'pending_approval';
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS progress_percent INTEGER NOT NULL DEFAULT 0;
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS rollback_snapshot_ref TEXT;
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS provider_deploy_id TEXT;
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS installed_version TEXT;
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS installed_commit_sha TEXT;
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS health_verified_at TEXT;
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS cancelled_at TEXT;
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;

-- 5. DEPLOYMENT LOGS TABLE
CREATE TABLE IF NOT EXISTS public.deployment_logs (
    id BIGSERIAL PRIMARY KEY,
    deployment_id TEXT NOT NULL REFERENCES public.deployments(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- 6. DEPLOYMENT LOCKS TABLE
CREATE TABLE IF NOT EXISTS public.deployment_locks (
    client_id TEXT NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
    component TEXT NOT NULL,
    deployment_id TEXT NOT NULL REFERENCES public.deployments(id) ON DELETE CASCADE,
    lock_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(client_id, component)
);

-- 7. SUPPORT TICKETS TABLE
CREATE TABLE IF NOT EXISTS public.support_tickets (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
    external_number TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'medium',
    component TEXT NOT NULL DEFAULT 'services',
    status TEXT NOT NULL DEFAULT 'escalated',
    submitted_by_role TEXT NOT NULL DEFAULT 'client_admin',
    approved_by TEXT NOT NULL,
    diagnostics_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(client_id, external_number)
);

-- 8. CLIENT INFRASTRUCTURE TABLE
CREATE TABLE IF NOT EXISTS public.client_infrastructure (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    component TEXT NOT NULL,
    provider_resource_id TEXT,
    region TEXT,
    instance_size TEXT,
    domain TEXT,
    ip_address TEXT,
    credentials_ref TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    environment_variables_json TEXT NOT NULL DEFAULT '{}',
    config_version INTEGER NOT NULL DEFAULT 1,
    sync_status TEXT NOT NULL DEFAULT 'pending',
    last_synced_at TEXT,
    config_updated_at TEXT,
    config_updated_by TEXT,
    status TEXT NOT NULL DEFAULT 'provisioning',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
ALTER TABLE public.client_infrastructure ADD COLUMN IF NOT EXISTS config_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE public.client_infrastructure ADD COLUMN IF NOT EXISTS environment_variables_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE public.client_infrastructure ADD COLUMN IF NOT EXISTS config_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE public.client_infrastructure ADD COLUMN IF NOT EXISTS sync_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE public.client_infrastructure ADD COLUMN IF NOT EXISTS last_synced_at TEXT;
ALTER TABLE public.client_infrastructure ADD COLUMN IF NOT EXISTS config_updated_at TEXT;
ALTER TABLE public.client_infrastructure ADD COLUMN IF NOT EXISTS config_updated_by TEXT;

-- 9. CLIENT CONFIGURATION VERSIONS TABLE
CREATE TABLE IF NOT EXISTS public.client_configuration_versions (
    id BIGSERIAL PRIMARY KEY,
    infrastructure_id TEXT NOT NULL REFERENCES public.client_infrastructure(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    environment_variables_json TEXT NOT NULL DEFAULT '{}',
    sync_status TEXT NOT NULL DEFAULT 'pending',
    changed_by TEXT NOT NULL,
    change_summary TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(infrastructure_id, version)
);

-- 10. MANAGER SETTINGS TABLE
CREATE TABLE IF NOT EXISTS public.manager_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

-- 11. AUDIT EVENTS TABLE
CREATE TABLE IF NOT EXISTS public.audit_events (
    id BIGSERIAL PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT,
    client_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

-- 12. GITHUB WEBHOOK EVENTS TABLE
CREATE TABLE IF NOT EXISTS public.github_webhook_events (
    delivery_id TEXT PRIMARY KEY,
    event_name TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    repository_url TEXT,
    action TEXT,
    processed_status TEXT NOT NULL DEFAULT 'received',
    result_json TEXT NOT NULL DEFAULT '{}',
    received_at TEXT NOT NULL,
    processed_at TEXT
);

-- ==============================================================================
-- INDEXES FOR PERFORMANCE
-- ==============================================================================
CREATE INDEX IF NOT EXISTS idx_deployments_client ON public.deployments(client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_client ON public.support_tickets(client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_created ON public.audit_events(created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_deployments_idempotency ON public.deployments(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_releases_commit ON public.releases(component, repository_url, git_commit_sha);
CREATE INDEX IF NOT EXISTS idx_config_versions ON public.client_configuration_versions(infrastructure_id, version DESC);

-- ==============================================================================
-- SUPABASE ROW LEVEL SECURITY (RLS) & ACCESS CONTROL
-- ==============================================================================
ALTER TABLE public.clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.client_agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.releases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.deployments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.deployment_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.deployment_locks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.support_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.client_infrastructure ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.client_configuration_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.manager_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.github_webhook_events ENABLE ROW LEVEL SECURITY;

GRANT ALL ON ALL TABLES IN SCHEMA public TO postgres, service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO postgres, service_role;
