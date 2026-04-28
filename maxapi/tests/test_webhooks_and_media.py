from io import BytesIO

from tests.conftest import DEMO_ACCOUNT_ID


def test_webhook_subscription_lifecycle(client, auth_headers):
    create = client.post(
        "/v1/webhooks/subscriptions",
        json={
            "url": "https://example.com/hook",
            "secret": "supersecretvalue1234",
            "events": ["publication.published", "publication.failed"],
        },
        headers=auth_headers,
    )
    assert create.status_code == 201
    subscription_id = create.json()["subscription_id"]

    listed = client.get("/v1/webhooks/subscriptions", headers=auth_headers)
    assert any(
        item["subscription_id"] == subscription_id for item in listed.json()["items"]
    )

    delete = client.delete(
        f"/v1/webhooks/subscriptions/{subscription_id}", headers=auth_headers
    )
    assert delete.status_code == 204


def test_media_upload_and_import(client, auth_headers):
    upload = client.post(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/media",
        headers=auth_headers,
        data={"type": "image", "filename": "photo.jpg", "caption": "demo"},
        files={"file": ("photo.jpg", BytesIO(b"FAKEJPEG"), "image/jpeg")},
    )
    assert upload.status_code == 201
    media_id = upload.json()["media_id"]
    assert media_id.startswith("med_")

    imported = client.post(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/media/import",
        headers=auth_headers,
        json={
            "url": "https://example.com/photo.jpg",
            "type": "image",
            "source_post_id": "sample-collection-1",
        },
    )
    assert imported.status_code == 201
    assert imported.json()["status"] == "ready"


def test_media_import_rejects_non_http(client, auth_headers):
    response = client.post(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/media/import",
        headers=auth_headers,
        json={"url": "ftp://example.com/file", "type": "document"},
    )
    assert response.status_code == 400
