import unittest

from backend import app as cadence_app


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, db, table_name):
        self.db = db
        self.table_name = table_name
        self.filters = []
        self.selected_columns = None
        self.insert_payload = None
        self.update_payload = None
        self.limit_value = None

    def select(self, columns="*"):
        if columns != "*":
            self.selected_columns = [
                column.strip()
                for column in str(columns).split(",")
                if column.strip()
            ]
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def insert(self, payload):
        self.insert_payload = payload
        return self

    def update(self, payload):
        self.update_payload = payload
        return self

    def execute(self):
        rows = self.db.setdefault(self.table_name, [])

        if self.insert_payload is not None:
            payloads = (
                self.insert_payload
                if isinstance(self.insert_payload, list)
                else [self.insert_payload]
            )
            inserted = []
            for payload in payloads:
                row = dict(payload)
                self.db.add_ids(self.table_name, row)
                rows.append(row)
                inserted.append(row)
            return FakeResult(inserted)

        matched = list(rows)
        for column, value in self.filters:
            matched = [row for row in matched if row.get(column) == value]

        if self.update_payload is not None:
            for row in matched:
                row.update(self.update_payload)
            return FakeResult(matched)

        if self.limit_value is not None:
            matched = matched[: self.limit_value]
        if self.selected_columns is not None:
            matched = [
                {
                    column: row[column]
                    for column in self.selected_columns
                    if column in row
                }
                for row in matched
            ]
        return FakeResult(matched)


class FakeSupabase:
    ID_COLUMNS = {
        "api_keys": "api_key_id",
        "app_registrations": "app_registration_id",
        "applications": "application_id",
        "end_users": "end_user_id",
        "typing_samples": "typing_sample_id",
        "score_requests": "score_request_id",
    }

    def __init__(self):
        self.tables = {
            "applications": [
                {"application_id": "app-1", "name": "Demo", "allowed_origins": []}
            ],
            "app_registrations": [],
            "api_keys": [],
            "end_users": [],
            "typing_samples": [],
            "score_requests": [],
        }
        self.next_id = 1

    def table(self, table_name):
        return FakeQuery(self, table_name)

    def setdefault(self, table_name, default):
        return self.tables.setdefault(table_name, default)

    def add_ids(self, table_name, row):
        id_column = self.ID_COLUMNS.get(table_name)
        if id_column and id_column not in row:
            row[id_column] = f"{table_name}-{self.next_id}"
            self.next_id += 1


class FakeAuthObject:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeAuth:
    def __init__(self):
        self.users_by_token = {
            "confirmed-token": FakeAuthObject(
                id="developer-1",
                email="dev@partner.example",
                email_confirmed_at="2026-01-01T00:00:00+00:00",
            ),
            "other-token": FakeAuthObject(
                id="developer-2",
                email="other@partner.example",
                email_confirmed_at="2026-01-01T00:00:00+00:00",
            ),
            "unconfirmed-token": FakeAuthObject(
                id="developer-3",
                email="new@partner.example",
                email_confirmed_at=None,
            ),
        }

    def get_user(self, token):
        user = self.users_by_token.get(token)
        if not user:
            raise ValueError("bad token")
        return FakeAuthObject(user=user)

    def sign_up(self, payload=None, **kwargs):
        data = payload or kwargs
        return FakeAuthObject(
            user=FakeAuthObject(
                id="developer-4",
                email=data.get("email"),
                email_confirmed_at=None,
            ),
            session=None,
        )

    def sign_in_with_password(self, payload):
        email = payload.get("email")
        user = next(
            (
                candidate
                for candidate in self.users_by_token.values()
                if candidate.email == email
            ),
            None,
        )
        if not user:
            raise ValueError("invalid credentials")
        token = next(
            token
            for token, candidate in self.users_by_token.items()
            if candidate is user
        )
        return FakeAuthObject(
            user=user,
            session=FakeAuthObject(
                access_token=token,
                refresh_token="refresh-token",
                expires_at=1790000000,
                expires_in=3600,
            ),
        )


class FakeSupabaseAuthClient:
    def __init__(self):
        self.auth = FakeAuth()


class FakeModelService:
    enrollment_limit = 10

    def raw_data_to_sample(self, raw_data):
        return raw_data["keystrokes"]

    def score_against_enrollment(self, current_sample, enrollment_samples):
        return 0.82


class PlatformApiTest(unittest.TestCase):
    def setUp(self):
        self.original_supabase = cadence_app.supabase
        self.original_supabase_auth = cadence_app.supabase_auth
        self.original_model_service = cadence_app.model_service
        self.original_admin_token = cadence_app.ADMIN_TOKEN
        self.original_allow_open_admin = cadence_app.ALLOW_OPEN_ADMIN
        self.original_allowed_origins = set(cadence_app.ALLOWED_ORIGINS)
        self.fake_supabase = FakeSupabase()
        api_key = "sk_live_test_key_for_platform_flow"
        self.api_key = api_key
        self.fake_supabase.tables["api_keys"].append({
            "api_key_id": "key-1",
            "application_id": "app-1",
            "key_prefix": api_key[: cadence_app.API_KEY_PREFIX_LENGTH],
            "key_hash": cadence_app.hash_api_key(api_key),
            "revoked_at": None,
        })
        cadence_app.supabase = self.fake_supabase
        cadence_app.supabase_auth = FakeSupabaseAuthClient()
        cadence_app.model_service = FakeModelService()
        cadence_app.ADMIN_TOKEN = ""
        cadence_app.ALLOW_OPEN_ADMIN = True
        cadence_app.app.config["TESTING"] = True
        self.client = cadence_app.app.test_client()

    def tearDown(self):
        cadence_app.supabase = self.original_supabase
        cadence_app.supabase_auth = self.original_supabase_auth
        cadence_app.model_service = self.original_model_service
        cadence_app.ADMIN_TOKEN = self.original_admin_token
        cadence_app.ALLOW_OPEN_ADMIN = self.original_allow_open_admin
        cadence_app.ALLOWED_ORIGINS = self.original_allowed_origins

    def auth_headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def developer_headers(self, token="confirmed-token"):
        return {"Authorization": f"Bearer {token}"}

    def test_platform_enroll_and_score_flow(self):
        raw_data = {
            "keystrokes": [
                {"hold_time": 80, "flight_time": 0, "down_down": 0},
                {"hold_time": 82, "flight_time": 42, "down_down": 122},
            ]
        }

        for _index in range(cadence_app.REQUIRED_ENROLLMENT_SAMPLES):
            response = self.client.post(
                "/v1/enroll",
                json={"external_user_id": "user-123", "raw_data": raw_data},
                headers=self.auth_headers(),
            )
            self.assertEqual(response.status_code, 201)

        response = self.client.post(
            "/v1/score",
            json={"external_user_id": "user-123", "raw_data": raw_data},
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["enrolled"])
        self.assertTrue(body["accepted"])
        self.assertTrue(body["match"])
        self.assertEqual(body["score"], 0.82)
        self.assertEqual(body["confidence"], 0.82)
        self.assertEqual(body["reason"], "accepted")
        self.assertGreaterEqual(body["score_duration_ms"], 0)
        self.assertEqual(len(self.fake_supabase.tables["score_requests"]), 1)
        self.assertIn("score_duration_ms", self.fake_supabase.tables["score_requests"][0])

    def test_create_app_and_api_key(self):
        response = self.client.post(
            "/v1/apps",
            json={
                "name": "Partner App",
                "allowed_origins": ["https://partner.example"],
            },
        )
        self.assertEqual(response.status_code, 201)
        app_body = response.get_json()
        application = app_body["application"]
        self.assertEqual(application["name"], "Partner App")
        self.assertEqual(application["slug"], "partner-app")
        self.assertEqual(application["allowed_origins"], ["https://partner.example"])

        response = self.client.post(
            f"/v1/apps/{application['application_id']}/api-keys",
            json={"name": "production"},
        )
        self.assertEqual(response.status_code, 201)
        key_body = response.get_json()["api_key"]
        self.assertEqual(key_body["name"], "production")
        self.assertTrue(key_body["key"].startswith("sk_live_"))
        self.assertEqual(
            key_body["key_prefix"],
            key_body["key"][: cadence_app.API_KEY_PREFIX_LENGTH],
        )

        stored_key = self.fake_supabase.tables["api_keys"][-1]
        self.assertEqual(stored_key["key_prefix"], key_body["key_prefix"])
        self.assertEqual(stored_key["key_hash"], cadence_app.hash_api_key(key_body["key"]))
        self.assertNotIn("key", stored_key)

    def test_developer_signup_requires_email_confirmation(self):
        response = self.client.post(
            "/v1/developer/signup",
            json={"email": "new@partner.example", "password": "correct horse"},
        )

        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["status"], "signed_up")
        self.assertFalse(body["email_confirmed"])
        self.assertIsNone(body["session"])

    def test_confirmed_developer_login_create_app_and_key(self):
        response = self.client.post(
            "/v1/developer/login",
            json={"email": "dev@partner.example", "password": "correct horse"},
        )
        self.assertEqual(response.status_code, 200)
        session = response.get_json()["session"]
        self.assertEqual(session["access_token"], "confirmed-token")

        response = self.client.post(
            "/v1/developer/apps",
            json={
                "name": "Partner App",
                "slug": "partner-app",
                "allowed_origins": ["https://partner.example"],
                "key_name": "production",
            },
            headers=self.developer_headers(session["access_token"]),
        )

        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        application = body["application"]
        api_key = body["api_key"]
        self.assertEqual(application["developer_user_id"], "developer-1")
        self.assertEqual(application["contact_email"], "dev@partner.example")
        self.assertEqual(application["allowed_origins"], ["https://partner.example"])
        self.assertEqual(api_key["name"], "production")
        self.assertTrue(api_key["key"].startswith("sk_live_"))
        self.assertEqual(
            self.fake_supabase.tables["api_keys"][-1]["key_hash"],
            cadence_app.hash_api_key(api_key["key"]),
        )

        response = self.client.get(
            "/v1/developer/apps",
            headers=self.developer_headers(session["access_token"]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["applications"][0]["application_id"],
            application["application_id"],
        )

        response = self.client.get(
            f"/v1/developer/apps/{application['application_id']}/api-keys",
            headers=self.developer_headers(session["access_token"]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["api_keys"][0]["key_prefix"], api_key["key_prefix"])
        self.assertNotIn("key_hash", response.get_json()["api_keys"][0])

    def test_developer_login_rejects_unconfirmed_email(self):
        response = self.client.post(
            "/v1/developer/login",
            json={"email": "new@partner.example", "password": "correct horse"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["status"], "email_not_confirmed")

    def test_developer_cannot_manage_another_developers_app(self):
        self.fake_supabase.tables["applications"].append({
            "application_id": "owned-by-other",
            "name": "Other App",
            "slug": "other-app",
            "allowed_origins": [],
            "developer_user_id": "developer-2",
        })

        response = self.client.get(
            "/v1/developer/apps/owned-by-other/api-keys",
            headers=self.developer_headers("confirmed-token"),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["status"], "not_found")

    def test_admin_endpoints_fail_closed_without_admin_token(self):
        cadence_app.ALLOW_OPEN_ADMIN = False

        response = self.client.get("/v1/apps")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["status"], "misconfigured")
        self.assertEqual(
            response.get_json()["message"],
            "CADENCE_ADMIN_TOKEN is required for admin endpoints",
        )

    def test_admin_endpoints_accept_configured_admin_token(self):
        cadence_app.ALLOW_OPEN_ADMIN = False
        cadence_app.ADMIN_TOKEN = "x" * 32

        response = self.client.get(
            "/v1/apps",
            headers={"Authorization": f"Bearer {cadence_app.ADMIN_TOKEN}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_public_registration_request_and_admin_approval(self):
        response = self.client.post(
            "/v1/app-registrations",
            json={
                "name": "Partner App",
                "contact_email": "dev@partner.example",
                "allowed_origins": ["https://partner.example"],
                "use_case": "Login risk scoring",
            },
        )
        self.assertEqual(response.status_code, 201)
        registration = response.get_json()["registration"]
        lookup_token = response.get_json()["lookup_token"]
        self.assertTrue(lookup_token.startswith("reg_status_"))
        self.assertEqual(registration["status"], "pending")
        self.assertEqual(registration["contact_email"], "dev@partner.example")
        self.assertNotIn("lookup_token_hash", registration)

        response = self.client.get(
            f"/v1/app-registrations/{registration['app_registration_id']}/status",
            headers={"Authorization": f"Bearer {lookup_token}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["registration"]["status"], "pending")

        response = self.client.get("/v1/app-registrations")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("lookup_token_hash", response.get_json()["registrations"][0])
        self.assertEqual(
            response.get_json()["registrations"][0]["app_registration_id"],
            registration["app_registration_id"],
        )

        response = self.client.post(
            f"/v1/app-registrations/{registration['app_registration_id']}/approve",
            json={"key_name": "production"},
        )
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["status"], "approved")
        self.assertEqual(body["registration"]["status"], "approved")
        self.assertEqual(
            body["registration"]["application_id"],
            body["application"]["application_id"],
        )
        self.assertEqual(body["application"]["contact_email"], "dev@partner.example")
        self.assertEqual(body["api_key"]["name"], "production")
        self.assertTrue(body["api_key"]["key"].startswith("sk_live_"))

        response = self.client.get(
            f"/v1/app-registrations/{registration['app_registration_id']}/status",
            query_string={"lookup_token": lookup_token},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["registration"]["status"], "approved")
        self.assertEqual(
            response.get_json()["registration"]["application_id"],
            body["application"]["application_id"],
        )

    def test_registration_status_rejects_invalid_lookup_token(self):
        response = self.client.post(
            "/v1/app-registrations",
            json={
                "name": "Partner App",
                "contact_email": "dev@partner.example",
            },
        )
        self.assertEqual(response.status_code, 201)
        registration_id = response.get_json()["registration"]["app_registration_id"]

        response = self.client.get(
            f"/v1/app-registrations/{registration_id}/status",
            headers={"Authorization": "Bearer wrong"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["message"], "invalid lookup token")

    def test_public_registration_can_be_rejected(self):
        response = self.client.post(
            "/v1/app-registrations",
            json={
                "name": "Rejected App",
                "contact_email": "dev@rejected.example",
            },
        )
        self.assertEqual(response.status_code, 201)
        registration_id = response.get_json()["registration"]["app_registration_id"]

        response = self.client.post(f"/v1/app-registrations/{registration_id}/reject")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "rejected")
        self.assertEqual(body["registration"]["status"], "rejected")

    def test_list_apps_list_keys_and_revoke_key(self):
        response = self.client.get("/v1/apps")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["applications"][0]["application_id"], "app-1")

        response = self.client.get("/v1/apps/app-1/api-keys")
        self.assertEqual(response.status_code, 200)
        key_body = response.get_json()["api_keys"][0]
        self.assertEqual(key_body["api_key_id"], "key-1")
        self.assertNotIn("key_hash", key_body)

        response = self.client.post("/v1/api-keys/key-1/revoke")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "revoked")
        self.assertIsNotNone(body["api_key"]["revoked_at"])
        self.assertNotIn("key_hash", body["api_key"])

        response = self.client.get(
            "/v1/end-users/user-123",
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["message"], "invalid API key")

    def test_api_key_rejects_disallowed_origin(self):
        self.fake_supabase.tables["applications"][0]["allowed_origins"] = [
            "https://allowed.example"
        ]

        response = self.client.get(
            "/v1/end-users/user-123",
            headers={
                **self.auth_headers(),
                "Origin": "https://evil.example",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["status"], "forbidden")
        self.assertEqual(
            response.get_json()["message"],
            "origin is not allowed for this application",
        )

    def test_cors_preflight_allows_admin_token_header(self):
        cadence_app.ALLOWED_ORIGINS = {"https://console.example"}

        response = self.client.options(
            "/v1/apps",
            headers={
                "Origin": "https://console.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Cadence-Admin-Token",
            },
        )

        self.assertIn(response.status_code, {200, 204})
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"],
            "https://console.example",
        )
        self.assertIn(
            "X-Cadence-Admin-Token",
            response.headers["Access-Control-Allow-Headers"],
        )

    def test_app_usage_summary(self):
        self.fake_supabase.tables["api_keys"].append({
            "api_key_id": "key-2",
            "application_id": "app-1",
            "name": "old",
            "key_prefix": "sk_live_old",
            "key_hash": "hash",
            "revoked_at": "2026-01-01T00:00:00+00:00",
            "last_used_at": "2026-01-01T00:00:00+00:00",
        })
        self.fake_supabase.tables["end_users"].extend([
            {"end_user_id": "end-user-1", "application_id": "app-1", "external_user_id": "user-1"},
            {"end_user_id": "end-user-2", "application_id": "app-1", "external_user_id": "user-2"},
            {"end_user_id": "other-user", "application_id": "other-app", "external_user_id": "other"},
        ])
        for index in range(cadence_app.REQUIRED_ENROLLMENT_SAMPLES):
            self.fake_supabase.tables["typing_samples"].append({
                "typing_sample_id": f"sample-{index}",
                "application_id": "app-1",
                "end_user_id": "end-user-1",
                "successful": True,
                "source": "enrollment",
            })
        self.fake_supabase.tables["typing_samples"].append({
            "typing_sample_id": "sample-score",
            "application_id": "app-1",
            "end_user_id": "end-user-1",
            "successful": True,
            "source": "score",
        })
        self.fake_supabase.tables["typing_samples"].append({
            "typing_sample_id": "sample-other",
            "application_id": "other-app",
            "end_user_id": "other-user",
            "successful": True,
            "source": "enrollment",
        })
        self.fake_supabase.tables["score_requests"].extend([
            {
                "score_request_id": "score-1",
                "application_id": "app-1",
                "accepted": True,
                "reason": "accepted",
                "score_duration_ms": 12.5,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "score_request_id": "score-2",
                "application_id": "app-1",
                "accepted": False,
                "reason": "low_confidence",
                "score_duration_ms": 37.5,
                "created_at": "2026-01-02T00:00:00+00:00",
            },
            {
                "score_request_id": "score-other",
                "application_id": "other-app",
                "accepted": True,
                "reason": "accepted",
                "score_duration_ms": 1,
            },
        ])

        response = self.client.get("/v1/apps/app-1/usage")

        self.assertEqual(response.status_code, 200)
        usage = response.get_json()["usage"]
        self.assertEqual(usage["application"]["application_id"], "app-1")
        self.assertEqual(usage["api_keys"]["total"], 2)
        self.assertEqual(usage["api_keys"]["active"], 1)
        self.assertEqual(usage["api_keys"]["revoked"], 1)
        self.assertEqual(usage["end_users"]["total"], 2)
        self.assertEqual(usage["end_users"]["enrolled"], 1)
        self.assertEqual(usage["typing_samples"]["total"], cadence_app.REQUIRED_ENROLLMENT_SAMPLES + 1)
        self.assertEqual(usage["typing_samples"]["enrollment"], cadence_app.REQUIRED_ENROLLMENT_SAMPLES)
        self.assertEqual(usage["typing_samples"]["score_stored"], 1)
        self.assertEqual(usage["score_requests"]["total"], 2)
        self.assertEqual(usage["score_requests"]["accepted"], 1)
        self.assertEqual(usage["score_requests"]["rejected"], 1)
        self.assertEqual(usage["score_requests"]["acceptance_rate"], 0.5)
        self.assertEqual(usage["score_requests"]["avg_score_duration_ms"], 25)
        self.assertEqual(usage["score_requests"]["p95_score_duration_ms"], 37.5)
        self.assertEqual(
            usage["score_requests"]["reason_counts"],
            {"accepted": 1, "low_confidence": 1},
        )
        self.assertEqual(
            usage["score_requests"]["last_scored_at"],
            "2026-01-02T00:00:00+00:00",
        )

    def test_app_usage_returns_not_found_for_missing_application(self):
        response = self.client.get("/v1/apps/missing/usage")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["status"], "not_found")


if __name__ == "__main__":
    unittest.main()
