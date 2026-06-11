# ---------------------------------------------------------------------------
# End-user authentication flow
# ---------------------------------------------------------------------------
#
# These are the app-scoped routes used by the Synergyze demo and capture
# package: signup, keystroke login, 2FA verification/resend, logout, and fraud
# reporting links embedded in email.


@app.post("/signup")
@limiter.limit("5 per minute; 20 per hour")
@require_api_key
def signup():
    data = request.json
    email = data.get("email")
    password = data.get("password")
    username = data.get("username")

    if not email:
        return jsonify({"status": "error", "message": "missing email"}), 400
    if not password:
        return jsonify({"status": "error", "message": "missing password"}), 400
    if not username:
        return jsonify({"status": "error", "message": "missing username"}), 400

    policy_error = password_policy_error(password, username)
    if policy_error:
        return jsonify({"status": "error", "message": policy_error}), 400

    try:
        existing_user = supabase.table("user_profiles") \
            .select("username") \
            .eq("username", username) \
            .execute()
    except Exception as exc:
        app.logger.exception("signup username lookup failed")
        return jsonify({
            "status": "error",
            "message": f"could not check username availability: {exc}",
        }), 502

    if existing_user.data:
        return jsonify({"status": "error", "message": "username already exists"}), 400

    try:
        sign_up_result = _supabase_sign_up(email, password)
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    print("SIGN UP RESULT:", sign_up_result)
    sign_up_error = None
    sign_up_user = None
    if isinstance(sign_up_result, dict):
        sign_up_error = sign_up_result.get("error")
        sign_up_user = sign_up_result.get("user")
    else:
        sign_up_error = getattr(sign_up_result, "error", None)
        sign_up_user = getattr(sign_up_result, "user", None)

    if sign_up_error:
        return jsonify({"status": "error", "message": str(sign_up_error)}), 400

    user_id = None
    if isinstance(sign_up_user, dict):
        user_id = sign_up_user.get("id")
    else:
        user_id = getattr(sign_up_user, "id", None)

    if not user_id:
        return jsonify({"status": "error", "message": "signup did not return user id"}), 400

    try:
        supabase.table("user_profiles").insert({
            "user_id": user_id,
            "username": username,
            "email": email,
            "current_login_status": "logged in",
            "number_login_attempts": 0,
            "failed_password_attempts": 0,
            "number_of_successful_logins": 0,
            "application_id": request.cadence_application["application_id"],
        }).execute()
    except Exception as exc:
        app.logger.exception("signup profile insert failed for user_id=%s", user_id)
        try:
            supabase.auth.admin.delete_user(user_id)
        except Exception:
            app.logger.exception("signup rollback failed for user_id=%s", user_id)
        return jsonify({
            "status": "error",
            "message": f"could not create user profile: {exc}",
        }), 502

    return jsonify({"status": "signup_success", "user_id": user_id}), 200


@app.post("/password/change")
@limiter.limit("5 per minute; 20 per hour")
@require_api_key
def change_password():
    data = request.json or {}
    username = data.get("username")
    current_password = data.get("current_password")
    new_password = data.get("new_password")

    if not username:
        return jsonify({"status": "error", "message": "missing username"}), 400
    if not current_password:
        return jsonify({"status": "error", "message": "missing current_password"}), 400
    if not new_password:
        return jsonify({"status": "error", "message": "missing new_password"}), 400

    policy_error = password_policy_error(new_password, username)
    if policy_error:
        return jsonify({"status": "error", "message": policy_error}), 400

    profile, profile_error = get_app_user_profile(username=username)
    if profile_error == "forbidden":
        return error_response("forbidden", 403, "forbidden")
    if profile_error:
        return jsonify({"status": "user not found"}), 200

    try:
        sign_in_result = _supabase_sign_in(profile.get("email"), current_password)
    except Exception:
        return jsonify({"status": "error", "message": "invalid credentials"}), 401

    if auth_sign_in_error(sign_in_result):
        return jsonify({"status": "error", "message": "invalid credentials"}), 401

    try:
        _supabase_update_password(profile["user_id"], new_password)
    except Exception as exc:
        app.logger.exception("password update failed for user_id=%s", profile.get("user_id"))
        return jsonify({
            "status": "error",
            "message": f"could not update password: {exc}",
        }), 502

    supabase.table("user_profiles") \
        .update({
            "current_login_status": "not logged in",
            "failed_password_attempts": 0,
        }) \
        .eq("user_id", profile["user_id"]) \
        .execute()

    return jsonify({"status": "password_changed", "user_id": profile["user_id"]}), 200


@app.post("/users/unblock")
@limiter.limit("10 per minute; 100 per hour")
@require_api_key
def unblock_user():
    data = request.json or {}
    username = data.get("username")
    user_id = data.get("user_id")

    if not username and not user_id:
        return jsonify({"status": "error", "message": "missing username or user_id"}), 400

    profile, profile_error = get_app_user_profile(username=username, user_id=user_id)
    if profile_error == "forbidden":
        return error_response("forbidden", 403, "forbidden")
    if profile_error:
        return jsonify({"status": "user not found"}), 200

    supabase.table("user_profiles") \
        .update({"current_login_status": "not logged in"}) \
        .eq("user_id", profile["user_id"]) \
        .execute()

    return jsonify({
        "status": "unblocked",
        "user_id": profile["user_id"],
        "current_login_status": "not logged in",
    }), 200


@app.post("/authenticate")
@limiter.limit("10 per minute; 50 per hour")
@require_api_key
def authenticate():
    print("entering authenticate endpoint", flush=True)

    ip = get_remote_address()
    ip_result = supabase.table("blocked_ips").select("offense_count").eq("ip_address", ip).execute()
    if ip_result.data and ip_result.data[0]["offense_count"] >= 2:
        return jsonify({"status": "error", "message": "You are banned from this service."}), 403

    data = request.json
    username = data.get("username")
    password = data.get("password")
    raw_data = data.get("raw_data")
    is_mobile = bool(data.get("is_mobile"))

    if not username:
        return jsonify({"status": "error", "message": "missing username"}), 400
    if not password:
        return jsonify({"status": "error", "message": "missing password"}), 400
    if raw_data is None:
        return jsonify({"status": "error", "message": "missing raw_data"}), 400

    # Keep the paste/scripted-typing rejection before credential work so the
    # login form cannot submit synthetic timing data.
    if _is_scripted_typing(raw_data):
        return jsonify({"status": "error", "message": "please type your password manually"}), 400

    user = supabase.table("user_profiles") \
        .select("*") \
        .eq("username", username) \
        .execute()

    if not user.data:
        return jsonify({"status": "user not found"}), 200

    user_profile = user.data[0]
    user_id = user_profile.get("user_id")
    current_login_status = user_profile.get("current_login_status")

    if not user_id:
        return jsonify({"status": "error", "message": "user profile incomplete - missing user_id"}), 400

    if current_login_status == "pending 2fa":
        return jsonify({"status": "pending 2fa"}), 200
    elif current_login_status == "locked":
        return jsonify({"status": "account is locked"}), 200
    elif current_login_status == "password_locked":
        return jsonify({"status": "password_locked"}), 200
    elif current_login_status == "logged in":
        return jsonify({"status": "logged in"}), 200

    email = user_profile.get("email")
    try:
        sign_in_result = _supabase_sign_in(email, password)
    except Exception:
        return jsonify({"status": "error", "message": "invalid credentials"}), 401

    sign_in_error = None
    if isinstance(sign_in_result, dict):
        sign_in_error = sign_in_result.get("error")
    else:
        sign_in_error = getattr(sign_in_result, "error", None)

    if sign_in_error:
        lock_info = _handle_failed_password(
            user_id,
            user_profile.get("failed_password_attempts", 0),
        )
        if lock_info:
            body = {"status": "password_locked", "login_attempt_id": lock_info["login_attempt_id"]}
            if DEMO_MODE and lock_info.get("demo_otp"):
                body["demo_otp"] = lock_info["demo_otp"]
            return jsonify(body), 200
        return jsonify({"status": "error", "message": "invalid credentials"}), 401

    events_hash = _hash_events(raw_data)
    if events_hash and _is_replayed_payload(user_id, events_hash):
        return jsonify({"status": "error", "message": "duplicate keystroke payload — please retype your password"}), 400

    enrollment_count = count_successful_login_attempts(user_id)
    login_attempt_id = create_login_attempt(supabase, user_id, raw_data, events_hash=events_hash)
    if login_attempt_id == None:
        return jsonify({"status": "can't verify login"}), 200

    if enrollment_count < REQUIRED_ENROLLMENT_SAMPLES:
        return require_2fa(
            user_id,
            login_attempt_id,
            enrollment_count,
            "enrollment_required",
        )

    if is_mobile:
        return require_2fa(
            user_id,
            login_attempt_id,
            enrollment_count,
            "mobile_device",
        )

    score = get_score(user_id, raw_data, login_attempt_id)
    app.logger.info("score: %s", score)
    print("score =", score, flush=True)

    if score == None:
        score = 0.0

    supabase.table("login_attempts") \
        .update({"confidence_score": score}) \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    threshold = request.cadence_application.get("threshold") or DEFAULT_THRESHOLD
    app.logger.info("threshold: %s", threshold)

    if score >= threshold:
        enrollment_count += 1
        supabase.table("login_attempts") \
            .update({"successful_login": "successful"}) \
            .eq("login_attempt_id", login_attempt_id) \
            .execute()

        supabase.table("user_profiles") \
            .update({"current_login_status": "logged in", "failed_password_attempts": 0}) \
            .eq("user_id", user_id) \
            .execute()

        _increment_successful_logins(user_id)

        return jsonify({
            "status": "accepted",
            **enrollment_payload(enrollment_count),
        }), 200

    return require_2fa(
        user_id,
        login_attempt_id,
        enrollment_count,
        "low_confidence",
    )


@app.post("/logout")
@require_api_key
def logout():
    print("entering logout endpoint", flush=True)
    data = request.json or {}
    username = data.get("username")

    if not username:
        return jsonify({"status": "error", "message": "missing username"}), 400

    user = supabase.table("user_profiles") \
        .select("user_id") \
        .eq("username", username) \
        .execute()

    if not user.data:
        return jsonify({"status": "user not found"}), 200

    supabase.table("user_profiles") \
        .update({"current_login_status": "not logged in"}) \
        .eq("username", username) \
        .execute()

    return jsonify({"status": "logged out"}), 200


@app.post("/code_verification")
@require_api_key
def code_verification():
    ip = get_remote_address()
    ip_result = supabase.table("blocked_ips").select("offense_count").eq("ip_address", ip).execute()
    if ip_result.data and ip_result.data[0]["offense_count"] >= 2:
        return jsonify({"status": "error", "message": "You are banned from this service."}), 403

    data = request.json
    code = data.get("code")
    login_attempt_id = data.get("login_attempt_id")

    if not code:
        return jsonify({"status": "error", "message": "missing code"}), 400
    if not login_attempt_id:
        return jsonify({"status": "error", "message": "missing login_attempt_id"}), 400

    login_attempt_result = supabase.table("login_attempts") \
        .select("user_id") \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    if not login_attempt_result.data:
        return jsonify({"status": "rejected", "message": "invalid attempt"}), 200

    user_id = login_attempt_result.data[0].get("user_id")

    result = supabase.table("_2fa") \
        .select("*") \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    if not result.data:
        return jsonify({"status": "rejected", "message": "invalid attempt"}), 200

    entry = result.data[0]
    attempt_count = entry.get("attempt_count", 0)

    if attempt_count >= 3:
        supabase.table("user_profiles") \
            .update({"current_login_status": "locked"}) \
            .eq("user_id", user_id) \
            .execute()
        return jsonify({"status": "rejected", "message": "max attempts exceeded"}), 200

    code_hash = hashlib.sha256(code.encode()).hexdigest()
    stored_hash = entry.get("otp_hash")

    if code_hash != stored_hash:
        supabase.table("_2fa") \
            .update({"attempt_count": attempt_count + 1}) \
            .eq("login_attempt_id", login_attempt_id) \
            .execute()
        return jsonify({"status": "rejected"}), 200

    expires_at = entry.get("expires_at")
    if expires_at:
        expires_at = datetime.fromisoformat(expires_at)
        now = datetime.now(timezone.utc)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at:
            return jsonify({"status": "rejected", "message": "expired"}), 200

    supabase.table("_2fa").delete().eq("login_attempt_id", login_attempt_id).execute()

    profile_result = supabase.table("user_profiles") \
        .select("current_login_status") \
        .eq("user_id", user_id) \
        .single() \
        .execute()
    is_unlock = (profile_result.data or {}).get("current_login_status") == "password_locked"

    if is_unlock:
        supabase.table("user_profiles") \
            .update({"current_login_status": "not logged in", "failed_password_attempts": 0}) \
            .eq("user_id", user_id) \
            .execute()
        return jsonify({"status": "unlocked"}), 200

    supabase.table("login_attempts") \
        .update({"successful_login": "successful"}) \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    enrollment_count = count_successful_login_attempts(user_id)
    user_status = "logged in" if enrollment_count >= REQUIRED_ENROLLMENT_SAMPLES else "not logged in"

    supabase.table("user_profiles") \
        .update({"current_login_status": user_status, "failed_password_attempts": 0}) \
        .eq("user_id", user_id) \
        .execute()

    _increment_successful_logins(user_id)

    return jsonify({
        "status": "accepted",
        **enrollment_payload(enrollment_count),
    }), 200


@app.post("/resend_code")
@require_api_key
def resend_code():
    data = request.json
    login_attempt_id = data.get("login_attempt_id")

    if not login_attempt_id:
        return jsonify({"status": "error", "message": "missing login_attempt_id"}), 400

    login_attempt_result = supabase.table("login_attempts") \
        .select("user_id") \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    if not login_attempt_result.data:
        return jsonify({"status": "invalid attempt"}), 200

    user_id = login_attempt_result.data[0].get("user_id")

    result = supabase.table("_2fa") \
        .select("*") \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    if not result.data:
        return jsonify({"status": "invalid attempt"}), 200

    otp = str(random.randint(100000, 999999))
    otp_hash = hashlib.sha256(otp.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    supabase.table("_2fa") \
        .update({
            "otp_hash": otp_hash,
            "expires_at": expires_at.isoformat(),
            "attempt_count": 0
        }) \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    email_result = supabase.table("user_profiles") \
        .select("email") \
        .eq("user_id", user_id) \
        .execute()
    email = email_result.data[0]["email"]

    if DEMO_MODE:
        app.logger.warning("[DEMO_MODE] resent OTP for user_id=%s (%s): %s", user_id, email, otp)
        return jsonify({"status": "code sent", "demo_otp": otp}), 200

    resend.api_key = os.getenv("RESEND_KEY")
    resend.Emails.send({
        "from": RESEND_FROM_EMAIL,
        "to": email,
        "subject": "Verification Code",
        "html": f"<p>Your one-time code is: {otp}</p><p>Didn't attempt this login? <a href='{request.host_url}report-fraud/{login_attempt_id}'>Report it as fraud.</a></p>"
    })

    return jsonify({"status": "code sent"}), 200


@app.get("/report-fraud/<login_attempt_id>")
def report_fraud_confirm(login_attempt_id):
    return f"""
    <html><body>
      <p>Did you receive a verification code you didn't request?</p>
      <form method="POST" action="/report-fraud/{login_attempt_id}">
        <button type="submit">Yes, report this as fraud</button>
      </form>
    </body></html>
    """, 200


@app.post("/report-fraud/<login_attempt_id>")
def report_fraud(login_attempt_id):
    supabase.table("login_attempts") \
        .update({"successful_login": "fraud"}) \
        .eq("login_attempt_id", login_attempt_id) \
        .is_("successful_login", "null") \
        .execute()

    attempt = supabase.table("login_attempts") \
        .select("ip_address, user_id") \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    if attempt.data:
        row = attempt.data[0]
        user_id = row.get("user_id")
        if user_id:
            supabase.table("user_profiles") \
                .update({"current_login_status": "not logged in"}) \
                .eq("user_id", user_id) \
                .execute()
        ip = row.get("ip_address")
        if ip:
            existing = supabase.table("blocked_ips").select("offense_count").eq("ip_address", ip).execute()
            if existing.data:
                supabase.table("blocked_ips") \
                    .update({"offense_count": existing.data[0]["offense_count"] + 1}) \
                    .eq("ip_address", ip) \
                    .execute()
            else:
                supabase.table("blocked_ips").insert({"ip_address": ip, "offense_count": 1}).execute()

    return "<p>Thanks for letting us know. The login attempt has been flagged as fraud.</p>", 200
