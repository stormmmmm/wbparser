from tests.conftest import DEMO_ACCOUNT_ID, DEMO_CHANNEL_ID


def test_list_channels_includes_seed(client, auth_headers):
    response = client.get(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels", headers=auth_headers
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert any(item["channel_id"] == DEMO_CHANNEL_ID for item in items)


def test_resolve_channel_by_username(client, auth_headers):
    response = client.get(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels/resolve",
        params={"link": "@wb_finds_demo"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["channel_id"] == DEMO_CHANNEL_ID


def test_list_channels_filter_by_title(client, auth_headers):
    # exact match, case-insensitive by default
    response = client.get(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels",
        params={"title": "wb finds (demo)"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1 and items[0]["channel_id"] == DEMO_CHANNEL_ID

    # contains match
    response = client.get(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels",
        params={"title": "Finds", "title_match": "contains"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["channel_id"] == DEMO_CHANNEL_ID

    # no match
    response = client.get(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels",
        params={"title": "nonexistent"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_find_channel_by_title_endpoint(client, auth_headers):
    response = client.get(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels/find",
        params={"title": "WB Finds (demo)"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["channel_id"] == DEMO_CHANNEL_ID


def test_find_channel_by_title_not_found(client, auth_headers):
    response = client.get(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels/find",
        params={"title": "definitely-not-a-real-channel"},
        headers=auth_headers,
    )
    assert response.status_code == 404
    assert response.json()["code"] == "channel_not_found"


def test_publish_post_idempotent(client, auth_headers):
    body = {
        "external_id": "ext-1",
        "text": "Hello MAX!",
        "format": "plain",
    }
    first = client.post(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels/{DEMO_CHANNEL_ID}/posts",
        json=body,
        headers={**auth_headers, "Idempotency-Key": "idem-key-1"},
    )
    assert first.status_code == 201
    again = client.post(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels/{DEMO_CHANNEL_ID}/posts",
        json=body,
        headers={**auth_headers, "Idempotency-Key": "idem-key-1"},
    )
    assert again.status_code == 201
    assert first.json()["message_id"] == again.json()["message_id"]

    listed = client.get(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels/{DEMO_CHANNEL_ID}/posts",
        headers=auth_headers,
    )
    assert listed.status_code == 200
    assert any(
        p["message_id"] == first.json()["message_id"] for p in listed.json()["items"]
    )


def test_validate_post_rejects_huge_text(client, auth_headers):
    body = {"text": "Hello"}
    response = client.post(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels/{DEMO_CHANNEL_ID}/posts/validate",
        json=body,
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_edit_pin_unpin_delete_post(client, auth_headers):
    publish = client.post(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels/{DEMO_CHANNEL_ID}/posts",
        json={"text": "edit me"},
        headers=auth_headers,
    )
    message_id = publish.json()["message_id"]

    edited = client.put(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels/{DEMO_CHANNEL_ID}/posts/{message_id}",
        json={"text": "edited body"},
        headers=auth_headers,
    )
    assert edited.status_code == 200
    assert edited.json()["status"] == "edited"

    pin = client.put(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels/{DEMO_CHANNEL_ID}/posts/{message_id}/pin",
        json={"notify_subscribers": True},
        headers=auth_headers,
    )
    assert pin.status_code == 200
    assert pin.json()["ok"] is True

    unpin = client.delete(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels/{DEMO_CHANNEL_ID}/posts/{message_id}/pin",
        headers=auth_headers,
    )
    assert unpin.status_code == 200

    deleted = client.delete(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/channels/{DEMO_CHANNEL_ID}/posts/{message_id}",
        headers=auth_headers,
    )
    assert deleted.status_code == 204
