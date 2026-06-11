# ---------------------------------------------------------------------------
# Auth-flow internals
# ---------------------------------------------------------------------------
#
# This file is executed inside backend.app's module namespace. Keeping these
# helpers here makes app.py smaller while preserving the existing tests that
# monkeypatch backend.app.supabase, backend.app.model_service, and config flags.


def _handle_failed_password(user_id, current_failures):
    # Increment the consecutive wrong-password counter. Reaching the threshold
    # starts the email-unlock flow and pauses password login attempts.
    new_count = current_failures + 1

    if new_count < FAILED_PASSWORD_THRESHOLD:
        supabase.table("user_profiles") \
            .update({"failed_password_attempts": new_count}) \
            .eq("user_id", user_id) \
            .execute()
        return None

    # send_code stores an OTP row, so create a minimal login_attempt first to
    # anchor that challenge. If email delivery fails, leave the user unblocked
    # rather than stranding them without a recovery path.
    unlock_attempt_id = create_login_attempt(supabase, user_id, {})
    try:
        otp = send_code(user_id, unlock_attempt_id)
    except Exception:
        app.logger.exception("send_code failed during password-lock for user_id=%s", user_id)
        return None

    supabase.table("user_profiles") \
        .update({
            "failed_password_attempts": new_count,
            "current_login_status": "password_locked",
        }) \
        .eq("user_id", user_id) \
        .execute()

    return {
        "login_attempt_id": unlock_attempt_id,
        "demo_otp": otp if DEMO_MODE else None,
    }


# Thresholds derived from the observed automated 10ms-apart typing script.
# They are environment-backed so production can tune without code changes.
_MIN_MEAN_INTERVAL_MS = float(os.getenv("CADENCE_MIN_MEAN_INTERVAL_MS", "30"))
_MIN_STDDEV_INTERVAL_MS = float(os.getenv("CADENCE_MIN_STDDEV_INTERVAL_MS", "8"))


def _is_scripted_typing(raw_data):
    # Down-to-down intervals are the standard signal for typing rhythm. Very
    # low average timing or near-zero variance indicates scripted input.
    events = (raw_data.get("events") if isinstance(raw_data, dict) else raw_data) or []
    down_times = [e["t"] for e in events if e.get("type") == "down"]

    if len(down_times) < 4:
        return False

    intervals = [down_times[i] - down_times[i - 1] for i in range(1, len(down_times))]
    mean_ms = statistics.mean(intervals)
    stddev_ms = statistics.pstdev(intervals)
    return mean_ms < _MIN_MEAN_INTERVAL_MS or stddev_ms < _MIN_STDDEV_INTERVAL_MS


def _hash_events(raw_data):
    # Canonicalize the browser event stream before hashing so key ordering in
    # JSON cannot create two hashes for the same captured payload.
    events = (raw_data.get("events") if isinstance(raw_data, dict) else raw_data) or []
    if not events:
        return None
    canonical = json.dumps(events, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _is_replayed_payload(user_id, events_hash):
    # Treat exact event-stream reuse within 24 hours as replay. The window is
    # long enough for attack reuse and short enough to avoid old-login noise.
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    result = (
        supabase.table("login_attempts")
        .select("login_attempt_id")
        .eq("user_id", user_id)
        .eq("events_hash", events_hash)
        .gte("created_at", cutoff)
        .limit(1)
        .execute()
    )
    return bool(result.data)


def create_login_attempt(supabase, user_id, raw_data, events_hash=None):
    # The login_number is stored redundantly for easier audit/debug views.
    login_attempt_id = str(uuid.uuid4())
    profile = (
        supabase
        .table("user_profiles")
        .select("number_login_attempts")
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    current_count = profile.data["number_login_attempts"] or 0
    login_number = current_count + 1

    supabase.table("login_attempts").insert({
        "login_attempt_id": login_attempt_id,
        "user_id": user_id,
        "login_number": login_number,
        "two_fa_invoked": False,
        "successful_login": None,
        "confidence_score": None,
        "raw_data": raw_data or {},
        "events_hash": events_hash,
        "ip_address": get_remote_address(),
    }).execute()

    supabase.table("user_profiles") \
        .update({"number_login_attempts": login_number}) \
        .eq("user_id", user_id) \
        .execute()

    return login_attempt_id


def get_score(user_id, raw_data, login_attempt_id=None):
    # Delegate ML work to the model service so route handlers stay HTTP-focused.
    return model_service.score_login_attempt(
        supabase,
        user_id,
        raw_data,
        login_attempt_id=login_attempt_id,
    )


def send_code(user_id, login_attempt_id):
    # Store only the hash of the OTP. The plaintext is returned solely so demo
    # mode can display it without sending email.
    email_result = supabase.table("user_profiles") \
        .select("email") \
        .eq("user_id", user_id) \
        .execute()
    email = email_result.data[0]["email"]

    otp = str(random.randint(100000, 999999))
    otp_hash = hashlib.sha256(otp.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    supabase.table("_2fa") \
        .insert({
            "login_attempt_id": login_attempt_id,
            "otp_hash": otp_hash,
            "expires_at": expires_at.isoformat(),
            "attempt_count": 0
        }) \
        .execute()

    if DEMO_MODE:
        app.logger.warning("[DEMO_MODE] OTP for user_id=%s (%s): %s", user_id, email, otp)
        return otp

    resend.api_key = os.getenv("RESEND_KEY")
    resend.Emails.send({
        "from": RESEND_FROM_EMAIL,
        "to": email,
        "subject": "Verification Code",
        "html": f"<p>Your one-time code is: {otp}</p><p>Didn't attempt this login? <a href='{request.host_url}report-fraud/{login_attempt_id}'>Report it as fraud.</a></p>"
    })
    return otp
