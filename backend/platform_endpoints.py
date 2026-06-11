# ---------------------------------------------------------------------------
# Platform and operator endpoints
# ---------------------------------------------------------------------------
#
# These routes are for health checks, admin-token-managed applications/API
# keys, and app-scoped support operations performed with an sk_live API key.


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/model/health")
def model_health():
    return jsonify(model_service.health())


@app.post("/v1/apps")
@limiter.limit(ADMIN_RATE_LIMIT)
def create_platform_app():
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    application, app_error = create_application_record(get_json_body())
    if app_error:
        return app_error
    return jsonify({"status": "created", "application": application}), 201


@app.get("/v1/apps")
@limiter.limit(ADMIN_RATE_LIMIT)
def list_platform_apps():
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    result = supabase.table("applications") \
        .select("*") \
        .order("created_at", desc=True) \
        .execute()
    return jsonify({"status": "ok", "applications": result.data or []})


@app.get("/v1/apps/<application_id>/usage")
@limiter.limit(ADMIN_RATE_LIMIT)
def get_platform_app_usage(application_id):
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    usage = build_platform_app_usage(application_id)
    if usage is None:
        return error_response("application not found", 404, "not_found")
    return jsonify({"status": "ok", "usage": usage})


@app.post("/v1/apps/<application_id>/api-keys")
@limiter.limit(ADMIN_RATE_LIMIT)
def create_platform_api_key(application_id):
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    app_result = supabase.table("applications") \
        .select("application_id") \
        .eq("application_id", application_id) \
        .execute()
    if not (app_result.data or []):
        return error_response("application not found", 404, "not_found")

    data = get_json_body()
    key_row, api_key = create_api_key_record(application_id, data.get("name") or "default")
    return jsonify({
        "status": "created",
        "api_key": api_key_with_secret_response(key_row, api_key),
    }), 201


@app.get("/v1/apps/<application_id>/api-keys")
@limiter.limit(ADMIN_RATE_LIMIT)
def list_platform_api_keys(application_id):
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    app_result = supabase.table("applications") \
        .select("application_id") \
        .eq("application_id", application_id) \
        .execute()
    if not (app_result.data or []):
        return error_response("application not found", 404, "not_found")

    result = supabase.table("api_keys") \
        .select("api_key_id, application_id, name, key_prefix, revoked_at, last_used_at, created_at") \
        .eq("application_id", application_id) \
        .order("created_at", desc=True) \
        .execute()
    return jsonify({"status": "ok", "api_keys": result.data or []})


@app.post("/v1/api-keys/<api_key_id>/revoke")
@limiter.limit(ADMIN_RATE_LIMIT)
def revoke_platform_api_key(api_key_id):
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    result = supabase.table("api_keys") \
        .select("api_key_id, application_id, name, key_prefix, revoked_at, last_used_at, created_at") \
        .eq("api_key_id", api_key_id) \
        .execute()
    rows = result.data or []
    if not rows:
        return error_response("API key not found", 404, "not_found")

    key_row = rows[0]
    if key_row.get("revoked_at") is None:
        revoked_at = datetime.now(timezone.utc).isoformat()
        update_result = supabase.table("api_keys") \
            .update({"revoked_at": revoked_at}) \
            .eq("api_key_id", api_key_id) \
            .execute()
        key_row = (update_result.data or [key_row])[0]

    return jsonify({"status": "revoked", "api_key": public_api_key_row(key_row)})


@app.patch("/v1/apps/<application_id>/threshold")
@limiter.limit(PLATFORM_WRITE_RATE_LIMIT)
@require_api_key
def set_application_threshold(application_id):
    if request.cadence_application["application_id"] != application_id:
        return error_response("forbidden", 403, "forbidden")

    data = get_json_body()
    threshold = data.get("threshold")
    if threshold is None:
        return error_response("missing threshold")
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return error_response("threshold must be a number")
    if not (0.0 <= threshold <= 1.0):
        return error_response("threshold must be between 0 and 1")

    supabase.table("applications") \
        .update({"threshold": threshold}) \
        .eq("application_id", application_id) \
        .execute()

    return jsonify({"status": "ok", "application_id": application_id, "threshold": threshold})


_VALID_LOGIN_STATUSES = {"logged in", "not logged in"}


def _get_user_profile_for_app(user_id, application_id):
    """Return a user profile only when it belongs to the calling app."""
    result = supabase.table("user_profiles") \
        .select("user_id, current_login_status, application_id") \
        .eq("user_id", user_id) \
        .execute()
    row = (result.data or [None])[0]
    if not row:
        return None, "user not found"
    if row.get("application_id") != application_id:
        return None, "forbidden"
    return row, None


@app.get("/v1/apps/<application_id>/users/<user_id>/status")
@limiter.limit(PLATFORM_WRITE_RATE_LIMIT)
@require_api_key
def get_user_status(application_id, user_id):
    if request.cadence_application["application_id"] != application_id:
        return error_response("forbidden", 403, "forbidden")

    row, err = _get_user_profile_for_app(user_id, application_id)
    if err == "forbidden":
        return error_response("forbidden", 403, "forbidden")
    if err:
        return error_response("user not found", 404, "not found")

    return jsonify({"status": "ok", "user_id": user_id, "current_login_status": row["current_login_status"]})


@app.patch("/v1/apps/<application_id>/users/<user_id>/status")
@limiter.limit(PLATFORM_WRITE_RATE_LIMIT)
@require_api_key
def set_user_status(application_id, user_id):
    if request.cadence_application["application_id"] != application_id:
        return error_response("forbidden", 403, "forbidden")

    row, err = _get_user_profile_for_app(user_id, application_id)
    if err == "forbidden":
        return error_response("forbidden", 403, "forbidden")
    if err:
        return error_response("user not found", 404, "not found")

    data = get_json_body()
    new_status = data.get("current_login_status")
    if not new_status:
        return error_response("missing current_login_status")
    if new_status not in _VALID_LOGIN_STATUSES:
        return error_response(
            f"invalid status; must be one of: {', '.join(sorted(_VALID_LOGIN_STATUSES))}"
        )

    supabase.table("user_profiles") \
        .update({"current_login_status": new_status}) \
        .eq("user_id", user_id) \
        .execute()

    return jsonify({"status": "ok", "user_id": user_id, "current_login_status": new_status})
