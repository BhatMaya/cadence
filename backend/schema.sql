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
    allowed_origins jsonb not null default '[]'::jsonb,
    threshold double precision default 0.70,
    approved boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint applications_allowed_origins_array
        check (jsonb_typeof(allowed_origins) = 'array')
);

alter table if exists public.applications
    add column if not exists contact_email text,
    add column if not exists threshold double precision default 0.70,
    add column if not exists approved boolean not null default true;

create index if not exists applications_contact_email_created_idx
    on public.applications (contact_email, created_at desc);

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
