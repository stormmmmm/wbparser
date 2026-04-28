from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.repositories import Repository, write_ready_posts_jsonl
from app.schemas.metrics import PostPublicationResultIn
from app.schemas.post import ReadyPost
from app.utils.time import utcnow


def _parse_dt(value: str | None):
    if not value:
        return utcnow()
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class OutboxService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def export_ready_posts(
        self,
        session: Session,
        output: Path | None = None,
        limit: int = 100,
        post_type: str | None = None,
    ) -> tuple[int, list[ReadyPost]]:
        output_path = output or self.settings.outbox_path
        posts = Repository.get_next_ready_posts(session, limit=limit, post_type=post_type)
        ready_posts = [Repository.to_ready_post(session, post) for post in posts]
        count = write_ready_posts_jsonl(output_path, ready_posts)
        return count, ready_posts

    def import_publication_results(self, session: Session, input_path: Path | None = None) -> dict[str, int]:
        path = input_path or self.settings.inbox_publication_results_path
        if not path.exists():
            return {"processed": 0, "published": 0, "failed": 0}

        processed = 0
        published = 0
        failed = 0

        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                processed += 1
                payload = PostPublicationResultIn.model_validate_json(line)
                if payload.status == "published":
                    post = Repository.mark_post_published(
                        session,
                        post_id=payload.post_id,
                        telegram_message_id=payload.telegram_message_id or "",
                        telegram_url=payload.telegram_url,
                        published_at=payload.published_at or utcnow(),
                    )
                    if post:
                        published += 1
                elif payload.status == "failed":
                    post = Repository.mark_post_failed(
                        session,
                        post_id=payload.post_id,
                        retryable=payload.retryable,
                        error_message=payload.error_message,
                    )
                    if post:
                        failed += 1
        return {"processed": processed, "published": published, "failed": failed}

    def write_sample_json(self, output: Path, post: ReadyPost) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(post.model_dump_json(indent=2, ensure_ascii=False), encoding="utf-8")

    def append_ready_post_to_jsonl(self, output: Path, post: ReadyPost) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("a", encoding="utf-8") as fh:
            fh.write(post.model_dump_json(ensure_ascii=False))
            fh.write("\n")
