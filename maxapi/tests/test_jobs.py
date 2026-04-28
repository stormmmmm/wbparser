from datetime import datetime, timedelta, timezone

from tests.conftest import DEMO_ACCOUNT_ID, DEMO_CHANNEL_ID


def _ready_post(planned_at: datetime | None = None) -> dict:
    payload = {
        "post_id": "sample-collection-1",
        "post_type": "collection",
        "title": "Best WB picks",
        "text": "Top deals of the day",
        "fresh_until": (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(),
        "media": [
            {"position": 1, "url": "https://example.com/img1.jpg", "type": "image"}
        ],
        "buttons": [{"text": "Open", "url": "https://example.com"}],
        "source": "wb_parser",
    }
    if planned_at is not None:
        payload["planned_at"] = planned_at.isoformat()
    return payload


def test_create_publication_job_publishes_immediately(client, auth_headers):
    response = client.post(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/publication-jobs",
        json={"channel_id": DEMO_CHANNEL_ID, "ready_post": _ready_post()},
        headers={**auth_headers, "Idempotency-Key": "job-idem-1"},
    )
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "published"
    assert job["published_post"]["channel_id"] == DEMO_CHANNEL_ID

    listed = client.get(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/publication-jobs",
        headers=auth_headers,
    )
    assert listed.status_code == 200
    assert any(item["job_id"] == job["job_id"] for item in listed.json()["items"])


def test_create_publication_job_schedules_when_planned(client, auth_headers):
    publish_at = datetime.now(timezone.utc) + timedelta(hours=2)
    response = client.post(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/publication-jobs",
        json={
            "channel_id": DEMO_CHANNEL_ID,
            "ready_post": _ready_post(planned_at=publish_at),
            "mode": "schedule",
        },
        headers=auth_headers,
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "scheduled"
    assert body["scheduled_post"]["status"] == "scheduled"


def test_create_publication_job_dry_run(client, auth_headers):
    response = client.post(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/publication-jobs",
        json={
            "channel_id": DEMO_CHANNEL_ID,
            "ready_post": _ready_post(),
            "options": {"dry_run": True},
        },
        headers=auth_headers,
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "published"
    assert body["published_post"] is None


def test_cancel_publication_job_conflicts_when_published(client, auth_headers):
    create = client.post(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/publication-jobs",
        json={"channel_id": DEMO_CHANNEL_ID, "ready_post": _ready_post()},
        headers=auth_headers,
    )
    job_id = create.json()["job_id"]
    cancel = client.post(
        f"/v1/accounts/{DEMO_ACCOUNT_ID}/publication-jobs/{job_id}/cancel",
        headers=auth_headers,
    )
    assert cancel.status_code == 409
