-- Cadence backend schema. Run against the Postgres database backing
-- your Supabase stack (local or cloud). Idempotent: safe to re-run.

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
    created_at timestamptz not null default now()
);

create index if not exists login_attempts_user_idx
    on public.login_attempts (user_id, login_number desc);
create index if not exists login_attempts_username_success_idx
    on public.login_attempts (username, successful_login);

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
