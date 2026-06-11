# ---------------------------------------------------------------------------
# Developer portal endpoints
# ---------------------------------------------------------------------------
#
# This section owns developer signup/login, self-serve app/key management, and
# the manual registration review flow used by operators.


@app.post("/v1/developer/signup")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
def developer_signup():
    data = get_json_body()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not email:
        return error_response("missing email")
    if not password:
        return error_response("missing password")

    try:
        result = sign_up_with_password(email, password)
    except Exception as exc:
        return error_response(str(exc), 400)

    user = auth_user_payload(result)
    session = auth_session_payload(result)
    return jsonify({
        "status": "signed_up",
        "developer": user,
        "session": session,
        "email_confirmed": bool(user and user.get("email_confirmed_at")),
        "message": "Check your email to confirm your developer account.",
    }), 201


@app.post("/v1/developer/login")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
def developer_login():
    data = get_json_body()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not email:
        return error_response("missing email")
    if not password:
        return error_response("missing password")

    try:
        result = developer_sign_in(email, password)
    except Exception as exc:
        return error_response(str(exc), 401, "unauthorized")

    user = auth_user_payload(result)
    if not is_confirmed_developer(user):
        return error_response("developer email is not confirmed", 403, "email_not_confirmed")

    session = auth_session_payload(result)
    if not session or not session.get("access_token"):
        return error_response("developer session was not returned", 401, "unauthorized")

    return jsonify({
        "status": "ok",
        "developer": user,
        "session": session,
    })


@app.get("/v1/developer/me")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
@require_developer
def developer_me():
    return jsonify({
        "status": "ok",
        "developer": request.cadence_developer_user,
    })


@app.get("/v1/developer/apps")
@limiter.limit(PLATFORM_WRITE_RATE_LIMIT)
@require_developer
def list_developer_apps():
    result = supabase.table("applications") \
        .select("*") \
        .eq("contact_email", request.cadence_developer_user.get("email")) \
        .order("created_at", desc=True) \
        .execute()
    return jsonify({"status": "ok", "applications": result.data or []})


@app.post("/v1/developer/apps")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
@require_developer
def create_developer_app():
    data = get_json_body()
    developer_user = request.cadence_developer_user
    application, app_error = create_application_record({
        **data,
        "contact_email": developer_user.get("email"),
        "approved": True,
    })
    if app_error:
        return app_error

    key_row, api_key = create_api_key_record(
        application["application_id"],
        data.get("key_name") or "production",
    )
    return jsonify({
        "status": "created",
        "application": application,
        "api_key": api_key_with_secret_response(key_row, api_key),
    }), 201


@app.get("/v1/developer/apps/<application_id>/usage")
@limiter.limit(PLATFORM_WRITE_RATE_LIMIT)
@require_developer
def get_developer_app_usage(application_id):
    application, app_error = developer_application_or_error(application_id)
    if app_error:
        return app_error

    usage = build_platform_app_usage(application["application_id"])
    return jsonify({"status": "ok", "usage": usage})


@app.patch("/v1/developer/apps/<application_id>/threshold")
@limiter.limit(PLATFORM_WRITE_RATE_LIMIT)
@require_developer
def set_developer_app_threshold(application_id):
    application, app_error = developer_application_or_error(application_id)
    if app_error:
        return app_error

    data = get_json_body()
    threshold = data.get("threshold")
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return error_response("threshold must be one of 0.55, 0.68, or 0.72")

    allowed_thresholds = {0.55, 0.68, 0.72}
    if threshold not in allowed_thresholds:
        return error_response("threshold must be one of 0.55, 0.68, or 0.72")

    result = supabase.table("applications") \
        .update({
            "threshold": threshold,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }) \
        .eq("application_id", application["application_id"]) \
        .execute()
    updated_application = (result.data or [None])[0] or {
        **application,
        "threshold": threshold,
    }

    return jsonify({
        "status": "ok",
        "application": updated_application,
        "application_id": application["application_id"],
        "threshold": threshold,
    })


@app.get("/v1/developer/apps/<application_id>/api-keys")
@limiter.limit(PLATFORM_WRITE_RATE_LIMIT)
@require_developer
def list_developer_api_keys(application_id):
    application, app_error = developer_application_or_error(application_id)
    if app_error:
        return app_error

    result = supabase.table("api_keys") \
        .select("api_key_id, application_id, name, key_prefix, revoked_at, last_used_at, created_at") \
        .eq("application_id", application["application_id"]) \
        .order("created_at", desc=True) \
        .execute()
    return jsonify({"status": "ok", "api_keys": result.data or []})


@app.post("/v1/developer/apps/<application_id>/api-keys")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
@require_developer
def create_developer_api_key(application_id):
    application, app_error = developer_application_or_error(application_id)
    if app_error:
        return app_error

    data = get_json_body()
    key_row, api_key = create_api_key_record(
        application["application_id"],
        data.get("name") or "default",
    )
    return jsonify({
        "status": "created",
        "api_key": api_key_with_secret_response(key_row, api_key),
    }), 201


@app.post("/v1/developer/api-keys/<api_key_id>/revoke")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
@require_developer
def revoke_developer_api_key(api_key_id):
    result = supabase.table("api_keys") \
        .select("api_key_id, application_id, name, key_prefix, revoked_at, last_used_at, created_at") \
        .eq("api_key_id", api_key_id) \
        .execute()
    rows = result.data or []
    if not rows:
        return error_response("API key not found", 404, "not_found")

    key_row = rows[0]
    application = fetch_developer_application(
        key_row["application_id"],
        request.cadence_developer_user["user_id"],
        request.cadence_developer_user.get("email"),
    )
    if not application:
        return error_response("API key not found", 404, "not_found")

    if key_row.get("revoked_at") is None:
        revoked_at = datetime.now(timezone.utc).isoformat()
        update_result = supabase.table("api_keys") \
            .update({"revoked_at": revoked_at}) \
            .eq("api_key_id", api_key_id) \
            .execute()
        key_row = (update_result.data or [key_row])[0]

    return jsonify({"status": "revoked", "api_key": public_api_key_row(key_row)})


@app.post("/v1/app-registrations")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
def submit_platform_app_registration():
    data = get_json_body()
    contact_email = (data.get("contact_email") or "").strip()
    if not contact_email:
        return error_response("missing contact_email")

    lookup_token = generate_registration_lookup_token()
    application, app_error = create_application_record({
        **data,
        "contact_email": contact_email,
        "approved": False,
    })
    if app_error:
        return app_error

    return jsonify({
        "status": "submitted",
        "registration": public_app_registration_row(application),
        "lookup_token": lookup_token,
    }), 201


@app.get("/v1/app-registrations/<app_registration_id>/status")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
def get_platform_app_registration_status(app_registration_id):
    result = supabase.table("applications") \
        .select("*") \
        .eq("application_id", app_registration_id) \
        .execute()
    rows = result.data or []
    if not rows:
        return error_response("registration not found", 404, "not_found")

    return jsonify({
        "status": "ok",
        "registration": public_app_registration_row(rows[0]),
    })


@app.get("/v1/app-registrations")
@limiter.limit(ADMIN_RATE_LIMIT)
def list_platform_app_registrations():
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    result = supabase.table("applications") \
        .select("*") \
        .order("created_at", desc=True) \
        .execute()
    return jsonify({
        "status": "ok",
        "registrations": [
            public_app_registration_row(row)
            for row in result.data or []
        ],
    })


@app.post("/v1/app-registrations/<app_registration_id>/approve")
@limiter.limit(ADMIN_RATE_LIMIT)
def approve_platform_app_registration(app_registration_id):
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    result = supabase.table("applications") \
        .select("*") \
        .eq("application_id", app_registration_id) \
        .execute()
    rows = result.data or []
    if not rows:
        return error_response("registration not found", 404, "not_found")

    application = rows[0]
    if application.get("approved") is True:
        return jsonify({
            "status": "approved",
            "registration": public_app_registration_row(application),
            "application": application,
            "api_key": None,
        })

    data = get_json_body()
    key_row, api_key = create_api_key_record(
        application["application_id"],
        data.get("key_name") or "default",
    )

    update_result = supabase.table("applications") \
        .update({"approved": True}) \
        .eq("application_id", app_registration_id) \
        .execute()
    updated_application = (update_result.data or [application])[0]

    return jsonify({
        "status": "approved",
        "registration": public_app_registration_row(updated_application),
        "application": updated_application,
        "api_key": api_key_with_secret_response(key_row, api_key),
    }), 201


@app.post("/v1/app-registrations/<app_registration_id>/reject")
@limiter.limit(ADMIN_RATE_LIMIT)
def reject_platform_app_registration(app_registration_id):
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    result = supabase.table("applications") \
        .select("*") \
        .eq("application_id", app_registration_id) \
        .execute()
    rows = result.data or []
    if not rows:
        return error_response("registration not found", 404, "not_found")
    if rows[0].get("approved") is True:
        return error_response("registration already approved", 400)

    updated = supabase.table("applications") \
        .update({"approved": False}) \
        .eq("application_id", app_registration_id) \
        .execute()
    registration = public_app_registration_row((updated.data or rows)[0])
    registration["status"] = "rejected"
    return jsonify({
        "status": "rejected",
        "registration": registration,
    })
