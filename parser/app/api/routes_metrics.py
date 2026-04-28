from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.repositories import Repository
from app.db.session import get_session
from app.schemas.metrics import ClickMetricIn

router = APIRouter(prefix="/api/v1", tags=["metrics"])


@router.post("/clicks")
def add_click(payload: ClickMetricIn, session: Session = Depends(get_session)) -> dict[str, str]:
    click = Repository.add_click_metric(
        session=session,
        post_id=payload.post_id,
        article_id=payload.article_id,
        clicked_at=payload.clicked_at,
        click_source=payload.click_source,
        redirect_id=payload.redirect_id,
        raw_payload=payload.raw_payload,
    )
    if click is None:
        session.rollback()
        raise HTTPException(status_code=404, detail="post_not_found")
    session.commit()
    return {"status": "ok", "post_id": str(payload.post_id)}
