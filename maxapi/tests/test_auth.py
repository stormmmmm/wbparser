def test_protected_endpoints_require_token(client):
    response = client.get("/v1/accounts")
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "unauthorized"


def test_invalid_token_is_rejected(client):
    response = client.get(
        "/v1/accounts", headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401
