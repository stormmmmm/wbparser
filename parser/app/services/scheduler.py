from __future__ import annotations

import logging
import time

from app.config import Settings
from app.db.session import session_scope
from app.services.collect_candidates import CandidateCollectorService
from app.services.outbox import OutboxService
from app.services.post_builder import PostBuilderService
from app.services.score_products import ScoringService

logger = logging.getLogger(__name__)


def run_worker_once(settings: Settings) -> dict[str, int]:
    collector = CandidateCollectorService(settings)
    scorer = ScoringService(settings)
    post_builder = PostBuilderService(settings)
    outbox = OutboxService(settings)

    with session_scope() as session:
        collected = collector.collect(session, source="trend", limit=120)
        scored = scorer.score_products_in_db(session, limit=500)
        built_ids = post_builder.build_posts(session, post_type="collection", limit=10)
        exported_count, _ = outbox.export_ready_posts(session, limit=50)
        logger.info(
            "worker summary collected=%s scored=%s built=%s exported=%s",
            collected,
            scored,
            len(built_ids),
            exported_count,
        )
        return {
            "collected": collected["processed"],
            "scored": scored["scored"],
            "built": len(built_ids),
            "exported": exported_count,
        }


def run_worker_loop(settings: Settings, interval_seconds: int = 300) -> None:
    while True:
        try:
            run_worker_once(settings)
        except Exception:
            logger.exception("Worker loop iteration failed")
        time.sleep(interval_seconds)
