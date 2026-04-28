from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
import uvicorn

from app.config import get_settings
from app.db.session import init_db, session_scope
from app.logging_config import setup_logging
from app.services.collect_candidates import CandidateCollectorService
from app.services.outbox import OutboxService
from app.services.post_builder import PostBuilderService
from app.services.scheduler import run_worker_loop, run_worker_once
from app.services.score_products import ScoringService

app = typer.Typer(add_completion=False, help="WB parser CLI")


@app.command("init-db")
def init_db_cmd() -> None:
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    init_db()
    typer.echo("DB initialized")


@app.command("collect")
def collect_cmd(
    source: str = typer.Option(..., help="search|category|manual|trend"),
    query: Optional[str] = typer.Option(None, help="Search query for source=search"),
    category_id: Optional[str] = typer.Option(None, help="Category id for source=category"),
    article_id: list[str] = typer.Option(None, help="Article id list for source=manual"),
    limit: int = typer.Option(100, help="Max products"),
    premium_mode: bool = typer.Option(False, help="Allow premium price range"),
) -> None:
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    with session_scope() as session:
        collector = CandidateCollectorService(settings)
        result = collector.collect(
            session=session,
            source=source,
            query=query,
            category_id=category_id,
            article_ids=article_id,
            limit=limit,
            premium_mode=premium_mode,
        )
    typer.echo(result)


@app.command("refresh")
def refresh_cmd(article_id: str = typer.Option(..., help="WB article id")) -> None:
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    with session_scope() as session:
        collector = CandidateCollectorService(settings)
        result = collector.refresh_article(session, article_id=article_id)
    typer.echo(result)


@app.command("score")
def score_cmd(limit: int = typer.Option(500, help="Scoring batch size")) -> None:
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    with session_scope() as session:
        scorer = ScoringService(settings)
        result = scorer.score_products_in_db(session, limit=limit)
    typer.echo(result)


@app.command("build-posts")
def build_posts_cmd(
    type: str = typer.Option("collection", "--type", help="single|collection"),
    limit: int = typer.Option(10, help="Number of posts"),
) -> None:
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    with session_scope() as session:
        builder = PostBuilderService(settings)
        created = builder.build_posts(session, post_type=type, limit=limit)
    typer.echo({"created": len(created), "post_ids": created})


@app.command("export-ready")
def export_ready_cmd(
    format: str = typer.Option("jsonl", "--format", help="Only jsonl is supported"),
    output: Path = typer.Option(Path("../data/outbox/ready_posts.jsonl")),
    limit: int = typer.Option(100),
    post_type: Optional[str] = typer.Option(None),
) -> None:
    if format.lower() != "jsonl":
        raise typer.BadParameter("Only jsonl is supported")
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    output_path = output if output.is_absolute() else (settings.parser_dir / output).resolve()
    with session_scope() as session:
        outbox = OutboxService(settings)
        count, _ = outbox.export_ready_posts(session, output=output_path, limit=limit, post_type=post_type)
    typer.echo({"exported": count, "output": str(output_path)})


@app.command("import-publication-results")
def import_publication_results_cmd(
    input: Path = typer.Option(Path("../data/inbox/publication_results.jsonl")),
) -> None:
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    input_path = input if input.is_absolute() else (settings.parser_dir / input).resolve()
    with session_scope() as session:
        outbox = OutboxService(settings)
        result = outbox.import_publication_results(session, input_path=input_path)
    typer.echo(result)


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("0.0.0.0"),
    port: int = typer.Option(8000),
) -> None:
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    uvicorn.run("app.api.main:app", host=host, port=port, reload=False)


@app.command("worker")
def worker_cmd(
    loop: bool = typer.Option(False, "--loop", help="Run loop"),
    interval: int = typer.Option(300, help="Loop interval seconds"),
) -> None:
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    if loop:
        run_worker_loop(settings, interval_seconds=interval)
    else:
        result = run_worker_once(settings)
        typer.echo(result)


if __name__ == "__main__":
    app()
