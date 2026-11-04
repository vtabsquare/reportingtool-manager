-- VTAB Reporting Manager 0.4 centralized deployment control plane
-- Optional manual bootstrap for a dedicated Supabase project.
-- The manager can also create these tables automatically through
-- VTAB_MANAGER_DATABASE_URL on its first startup.

create table if not exists clients (
  id text primary key, code text not null unique, name text not null,
  status text not null default 'active', environment text not null default 'production',
  contact_email text,
  services_url text, admin_url text, database_provider text not null default 'supabase',
  installed_services_version text, installed_admin_version text,
  last_health_status text not null default 'unknown', last_health_at text,
  created_at text not null, updated_at text not null
);

create table if not exists client_agents (
  id text primary key, client_id text not null references clients(id) on delete cascade,
  name text not null, token_hash text not null, status text not null default 'registered',
  version text, capabilities_json text not null default '[]', last_seen_at text, created_at text not null
);

create table if not exists releases (
  id text primary key, component text not null, version text not null,
  channel text not null default 'stable', status text not null default 'draft',
  artifact_url text not null, checksum_sha256 text not null, min_connector_version text,
  release_notes text not null default '', manifest_json text not null default '{}',
  git_commit_sha text, repository_url text, migration_index integer not null default 0,
  created_at text not null,
  unique(component, version, channel)
);

create table if not exists deployments (
  id text primary key, client_id text not null references clients(id) on delete cascade,
  release_id text not null references releases(id), status text not null default 'pending_approval',
  approved_by text, scheduled_for text, started_at text, finished_at text, previous_version text,
  target_version text not null, error_message text, rollback_available boolean not null default true,
  agent_notes text, deployment_type text not null default 'update', provider text,
  stage text not null default 'queued', progress_percent integer not null default 0,
  rollback_snapshot_ref text, created_at text not null, updated_at text not null
);

create table if not exists client_infrastructure (
  id text primary key, client_id text not null references clients(id) on delete cascade,
  component text not null, provider text not null, provider_resource_id text,
  region text, instance_size text, domain text, ip_address text, credentials_ref text,
  status text not null default 'provisioning', created_at text not null, updated_at text not null
);

create table if not exists deployment_logs (
  id bigserial primary key, deployment_id text not null references deployments(id) on delete cascade,
  stage text not null, level text not null default 'info', message text not null,
  details_json text not null default '{}', created_at text not null
);

create table if not exists support_tickets (
  id text primary key, client_id text not null references clients(id) on delete cascade,
  external_number text not null, title text not null, summary text not null,
  severity text not null default 'medium', component text not null default 'services',
  status text not null default 'escalated', submitted_by_role text not null default 'client_admin',
  approved_by text not null, diagnostics_json text not null default '{}',
  created_at text not null, updated_at text not null, unique(client_id, external_number)
);

create table if not exists audit_events (
  id bigserial primary key, actor text not null, action text not null,
  object_type text not null, object_id text, client_id text,
  details_json text not null default '{}', created_at text not null
);

create table if not exists manager_settings (
  key text primary key, value_json text not null default '{}', updated_at text not null
);

create index if not exists idx_deployments_client on deployments(client_id, created_at desc);
create index if not exists idx_infrastructure_client on client_infrastructure(client_id, component, created_at desc);
create index if not exists idx_deployment_logs on deployment_logs(deployment_id, id);
create index if not exists idx_tickets_client on support_tickets(client_id, created_at desc);
create index if not exists idx_audit_created on audit_events(created_at desc);

-- 0.5 controlled-update additions. Every statement is additive and safe to
-- run against an existing manager database.
alter table clients add column if not exists client_admin_token_hash text;
alter table releases add column if not exists validation_status text not null default 'pending';
alter table releases add column if not exists validation_details_json text not null default '{}';
alter table releases add column if not exists validated_by text;
alter table releases add column if not exists validated_at text;
alter table releases add column if not exists source_event_id text;
alter table releases add column if not exists superseded_by_release_id text;
alter table deployments add column if not exists idempotency_key text;
alter table deployments add column if not exists provider_deploy_id text;
alter table deployments add column if not exists installed_version text;
alter table deployments add column if not exists installed_commit_sha text;
alter table deployments add column if not exists health_verified_at text;
alter table deployments add column if not exists cancelled_at text;
alter table deployments add column if not exists retry_count integer not null default 0;
alter table client_infrastructure add column if not exists config_json text not null default '{}';
alter table client_infrastructure add column if not exists environment_variables_json text not null default '{}';
alter table client_infrastructure add column if not exists config_version integer not null default 1;
alter table client_infrastructure add column if not exists sync_status text not null default 'pending';
alter table client_infrastructure add column if not exists last_synced_at text;
alter table client_infrastructure add column if not exists config_updated_at text;
alter table client_infrastructure add column if not exists config_updated_by text;

create table if not exists client_configuration_versions (
  id bigserial primary key, infrastructure_id text not null references client_infrastructure(id) on delete cascade,
  client_id text not null references clients(id) on delete cascade, version integer not null,
  config_json text not null default '{}', environment_variables_json text not null default '{}',
  sync_status text not null default 'pending', changed_by text not null, change_summary text,
  created_at text not null, unique(infrastructure_id,version)
);
create table if not exists github_webhook_events (
  delivery_id text primary key, event_name text not null, payload_sha256 text not null,
  repository_url text, action text, processed_status text not null default 'received',
  result_json text not null default '{}', received_at text not null, processed_at text
);
create table if not exists deployment_locks (
  client_id text not null references clients(id) on delete cascade, component text not null,
  deployment_id text not null references deployments(id) on delete cascade, lock_token text not null,
  expires_at text not null, created_at text not null, primary key(client_id,component)
);
create unique index if not exists idx_deployments_idempotency on deployments(idempotency_key) where idempotency_key is not null;
create index if not exists idx_releases_commit on releases(component,repository_url,git_commit_sha);
create index if not exists idx_config_versions on client_configuration_versions(infrastructure_id,version desc);
