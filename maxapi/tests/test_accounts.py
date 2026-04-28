from tests.conftest import DEMO_ACCOUNT_ID


def test_list_accounts_returns_seed(client, auth_headers):
    response = client.get("/v1/accounts", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["next_cursor"] is None
    ids = [item["account_id"] for item in payload["items"]]
    assert DEMO_ACCOUNT_ID in ids


def test_login_flow_creates_new_account(client, auth_headers):
    start = client.post(
        "/v1/accounts/login/start",
        headers=auth_headers,
        json={"phone": "+79991234567", "device_name": "Test Device"},
    )
    assert start.status_code == 202
    challenge_id = start.json()["challenge_id"]

    verify = client.post(
        "/v1/accounts/login/verify",
        headers=auth_headers,
        json={"challenge_id": challenge_id, "code": "000000"},
    )
    assert verify.status_code == 200
    account = verify.json()["account"]
    assert account["status"] == "connected"

    status_response = client.get(
        f"/v1/accounts/{account['account_id']}/status", headers=auth_headers
    )
    assert status_response.status_code == 200
    assert status_response.json()["can_publish"] is True


def test_logout_removes_account(client, auth_headers):
    start = client.post(
        "/v1/accounts/login/start",
        headers=auth_headers,
        json={"phone": "+79991234567"},
    )
    challenge_id = start.json()["challenge_id"]
    verify = client.post(
        "/v1/accounts/login/verify",
        headers=auth_headers,
        json={"challenge_id": challenge_id, "code": "000000"},
    )
    account_id = verify.json()["account"]["account_id"]

    logout = client.post(f"/v1/accounts/{account_id}/logout", headers=auth_headers)
    assert logout.status_code == 204

    missing = client.get(f"/v1/accounts/{account_id}", headers=auth_headers)
    assert missing.status_code == 404


def test_login_verify_rejects_bad_code(client, auth_headers):
    start = client.post(
        "/v1/accounts/login/start",
        headers=auth_headers,
        json={"phone": "+79991234567"},
    )
    challenge_id = start.json()["challenge_id"]
    verify = client.post(
        "/v1/accounts/login/verify",
        headers=auth_headers,
        json={"challenge_id": challenge_id, "code": "9999"},
    )
    assert verify.status_code == 409
