-- Cadence backend schema. Run against the Postgres database backing
-- your Supabase stack (local or cloud). Idempotent: safe to re-run.
--
-- Migration for existing databases: adds events_hash to login_attempts.
-- The CREATE TABLE block above already includes it for fresh installs.
alter table if exists public.login_attempts
    add column if not exists events_hash text;

alter table if exists public.user_profiles
    add column if not exists failed_password_attempts integer not null default 0;

create index if not exists login_attempts_replay_idx
    on public.login_attempts (user_id, events_hash, created_at);

create extension if not exists "pgcrypto";

create table if not exists public.app_registrations (
    app_registration_id uuid primary key default gen_random_uuid(),
    name text not null,
    slug text not null,
    contact_email text not null,
    allowed_origins jsonb not null default '[]'::jsonb,
    use_case text,
    lookup_token_hash text,
    status text not null default 'pending',
    application_id uuid,
    reviewed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint app_registrations_allowed_origins_array
        check (jsonb_typeof(allowed_origins) = 'array'),
    constraint app_registrations_status_check
        check (status in ('pending', 'approved', 'rejected'))
);

alter table if exists public.app_registrations
    add column if not exists contact_email text,
    add column if not exists allowed_origins jsonb not null default '[]'::jsonb,
    add column if not exists use_case text,
    add column if not exists lookup_token_hash text,
    add column if not exists status text not null default 'pending',
    add column if not exists application_id uuid,
    add column if not exists reviewed_at timestamptz;

create index if not exists app_registrations_status_created_idx
    on public.app_registrations (status, created_at desc);

-- One row per Supabase auth user. Mirrors auth.users for app-level
-- state we don't want to stuff into auth metadata.
create table if not exists public.user_profiles (
    user_id uuid primary key references auth.users(id) on delete cascade,
    username text not null unique,
    email text not null,
    threshold real not null default 0.5,
    current_login_status text,
    number_login_attempts integer not null default 0,
    -- Counts consecutive wrong passwords; resets to 0 on any successful auth.
    -- Reaching the threshold triggers a password-unlock email flow.
    failed_password_attempts integer not null default 0,
    created_at timestamptz not null default now()
);

create index if not exists user_profiles_username_idx
    on public.user_profiles (username);

-- One row per /authenticate call. Stores raw keystroke data plus the
-- model's similarity score so successful rows double as enrollment
-- samples for future logins.
create table if not exists public.login_attempts (
    login_attempt_id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    username text not null,
    login_number integer not null,
    two_fa_invoked boolean not null default false,
    successful_login boolean,
    confidence_score real,
    raw_data jsonb,
    -- SHA-256 of the canonicalized events array; used to detect replayed payloads.
    events_hash text,
    created_at timestamptz not null default now()
);

create index if not exists login_attempts_user_idx
    on public.login_attempts (user_id, login_number desc);
create index if not exists login_attempts_username_success_idx
    on public.login_attempts (username, successful_login);
-- Supports the replay-detection query: (user_id, events_hash) within a time window.
create index if not exists login_attempts_replay_idx
    on public.login_attempts (user_id, events_hash, created_at);

-- Pending OTP per login attempt. Deleted on success; expires_at gates
-- replay; attempt_count caps verification tries at 3.
create table if not exists public._2fa (
    login_attempt_id uuid primary key references public.login_attempts(login_attempt_id) on delete cascade,
    user_id uuid not null,
    username text not null,
    otp_hash text not null,
    expires_at timestamptz not null,
    attempt_count integer not null default 0,
    created_at timestamptz not null default now()
);

-- Platform API tables. These support third-party applications that use
-- Cadence as a typing-analysis service instead of the bundled demo auth UI.
create table if not exists public.applications (
    application_id uuid primary key default gen_random_uuid(),
    name text not null,
    slug text not null unique,
    contact_email text,
    app_registration_id uuid,
    developer_user_id uuid,
    allowed_origins jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint applications_allowed_origins_array
        check (jsonb_typeof(allowed_origins) = 'array')
);

alter table if exists public.applications
    add column if not exists contact_email text,
    add column if not exists app_registration_id uuid,
    add column if not exists developer_user_id uuid;

create index if not exists applications_developer_user_idx
    on public.applications (developer_user_id, created_at desc);

create table if not exists public.api_keys (
    api_key_id uuid primary key default gen_random_uuid(),
    application_id uuid not null references public.applications(application_id) on delete cascade,
    name text not null default 'default',
    key_prefix text not null unique,
    key_hash text not null,
    revoked_at timestamptz,
    last_used_at timestamptz,
    created_at timestamptz not null default now()
);

create index if not exists api_keys_application_idx
    on public.api_keys (application_id);
create index if not exists api_keys_key_prefix_idx
    on public.api_keys (key_prefix);

create table if not exists public.end_users (
    end_user_id uuid primary key default gen_random_uuid(),
    application_id uuid not null references public.applications(application_id) on delete cascade,
    external_user_id text not null,
    threshold real not null default 0.5,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (application_id, external_user_id),
    constraint end_users_metadata_object
        check (jsonb_typeof(metadata) = 'object'),
    constraint end_users_threshold_range
        check (threshold >= 0 and threshold <= 1)
);

create index if not exists end_users_application_external_idx
    on public.end_users (application_id, external_user_id);

create table if not exists public.typing_samples (
    typing_sample_id uuid primary key default gen_random_uuid(),
    application_id uuid not null references public.applications(application_id) on delete cascade,
    end_user_id uuid not null references public.end_users(end_user_id) on delete cascade,
    raw_data jsonb not null,
    source text not null default 'enrollment',
    successful boolean not null default true,
    quality_score real,
    confidence_score real,
    flags jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    constraint typing_samples_flags_array
        check (jsonb_typeof(flags) = 'array')
);

alter table if exists public.typing_samples
    alter column raw_data drop not null;

create index if not exists typing_samples_end_user_success_idx
    on public.typing_samples (end_user_id, successful, created_at desc);
create index if not exists typing_samples_application_idx
    on public.typing_samples (application_id, created_at desc);

create table if not exists public.score_requests (
    score_request_id uuid primary key default gen_random_uuid(),
    application_id uuid not null references public.applications(application_id) on delete cascade,
    end_user_id uuid not null references public.end_users(end_user_id) on delete cascade,
    external_user_id text not null,
    raw_data jsonb not null,
    score real,
    threshold real not null,
    accepted boolean not null,
    enrolled boolean not null,
    enrollment_count integer not null,
    enrollment_required integer not null,
    reason text,
    score_duration_ms real,
    created_at timestamptz not null default now()
);

alter table if exists public.score_requests
    alter column raw_data drop not null;

alter table if exists public.score_requests
    add column if not exists score_duration_ms real;

create index if not exists score_requests_end_user_created_idx
    on public.score_requests (end_user_id, created_at desc);
create index if not exists score_requests_application_created_idx
    on public.score_requests (application_id, created_at desc);
