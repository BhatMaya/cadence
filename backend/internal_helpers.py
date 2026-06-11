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


def password_policy_error(password, username):
    """Return the first password-policy error message, or None when valid."""
    if len(password) < 16:
        return "Password must be at least 16 characters."
    if not re.search(r"[A-Z]", password):
        return "Password must contain at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return "Password must contain at least one lowercase letter."
    if not re.search(r"[0-9]", password):
        return "Password must contain at least one number."
    if not re.search(r"[^A-Za-z0-9]", password):
        return "Password must contain at least one special character."
    if username and username.lower() in password.lower():
        return "Password must not contain your username."
    return None


def _supabase_update_password(user_id, new_password):
    """Update a Supabase Auth user's password through the admin client."""
    admin = supabase.auth.admin
    try:
        return admin.update_user_by_id(user_id, {"password": new_password})
    except TypeError:
        return admin.update_user_by_id(user_id, password=new_password)


def auth_sign_in_error(sign_in_result):
    """Normalize Supabase auth return shapes into an error object/string."""
    if isinstance(sign_in_result, dict):
        return sign_in_result.get("error")
    return getattr(sign_in_result, "error", None)


def get_app_user_profile(username=None, user_id=None):
    """Fetch a user profile that belongs to the API key's application."""
    if not username and not user_id:
        return None, "missing user identifier"

    query = supabase.table("user_profiles").select("*")
    if user_id:
        query = query.eq("user_id", user_id)
    else:
        query = query.eq("username", username)

    result = query.execute()
    rows = result.data or []
    if not rows:
        return None, "user not found"

    profile = rows[0]
    if profile.get("application_id") != request.cadence_application["application_id"]:
        return None, "forbidden"
    return profile, None


def security_email_html(otp, login_attempt_id):
    """Build the OTP email with both fraud-report and self-unblock options."""
    base_url = request.host_url.rstrip("/")
    report_url = f"{base_url}/report-fraud/{login_attempt_id}"
    unblock_url = f"{base_url}/unblock-login/{login_attempt_id}"
    return (
        f"<p>Your one-time code is: <strong>{otp}</strong></p>"
        "<p>If you requested this code but got stuck, you can clear the pending "
        f"login and start over: <a href='{unblock_url}'>unblock my login</a>.</p>"
        "<p>If you did not request this login, report it immediately: "
        f"<a href='{report_url}'>report it as fraud</a>.</p>"
    )


def password_change_prompt_html():
    """Return the post-fraud safety prompt shown from email-report links."""
    change_url = os.getenv("CADENCE_PASSWORD_CHANGE_URL", "").strip()
    change_link = (
        f"<p><a href='{change_url}'>Change your password now</a></p>"
        if change_url
        else "<p>Open the Cadence app and change your password before signing in again.</p>"
    )
    return (
        "<p>Thanks for letting us know. The login attempt has been flagged as fraud.</p>"
        "<p>Your account has been signed out to stop the pending login.</p>"
        "<h2>Change your password next</h2>"
        "<p>This report means someone may know your password. Choose a new password "
        "before trying to sign in again.</p>"
        f"{change_link}"
    )


def fetch_login_attempt(login_attempt_id, columns="ip_address, user_id"):
    """Return a login_attempts row by id, or None when the email link is stale."""
    result = supabase.table("login_attempts") \
        .select(columns) \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()
    return (result.data or [None])[0]


def clear_pending_login_attempt(login_attempt_id, reset_failures=False):
    """Clear the user login state attached to a pending email action link."""
    attempt = fetch_login_attempt(login_attempt_id)
    if not attempt:
        return None

    user_id = attempt.get("user_id")
    if user_id:
        update_payload = {"current_login_status": "not logged in"}
        if reset_failures:
            update_payload["failed_password_attempts"] = 0
        supabase.table("user_profiles") \
            .update(update_payload) \
            .eq("user_id", user_id) \
            .execute()

    supabase.table("_2fa") \
        .delete() \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()
    return attempt


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
        "html": security_email_html(otp, login_attempt_id),
    })
    return otp
