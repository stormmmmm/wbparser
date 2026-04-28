"""``wb-bridge`` command-line interface."""

from __future__ import annotations

import asyncio
import logging
import signal

import httpx
import typer

from bridge.config import BridgeSettings, load_settings
from bridge.maxapi_client import MaxApiClient
from bridge.parser_client import ParserClient
from bridge.worker import run_loop, run_once

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _require_targets(settings: BridgeSettings) -> None:
    if not settings.maxapi_account_id:
        raise typer.BadParameter(
            "WBBRIDGE_MAXAPI_ACCOUNT_ID is required (the MAX account that publishes)."
        )
    if not settings.maxapi_channel_id:
        raise typer.BadParameter(
            "WBBRIDGE_MAXAPI_CHANNEL_ID is required (the MAX channel to publish into)."
        )


def _build_clients(settings: BridgeSettings) -> tuple[
    httpx.AsyncClient, httpx.AsyncClient, ParserClient, MaxApiClient
]:
    parser_http = httpx.AsyncClient(
        base_url=settings.parser_base_url,
        timeout=settings.request_timeout_seconds,
    )
    maxapi_http = httpx.AsyncClient(
        base_url=settings.maxapi_base_url,
        timeout=settings.request_timeout_seconds,
    )
    parser = ParserClient(parser_http)
    maxapi = MaxApiClient(maxapi_http, token=settings.maxapi_token)
    return parser_http, maxapi_http, parser, maxapi


@app.command("run-once")
def cmd_run_once(
    post_type: str | None = typer.Option(
        None, "--post-type", help="Limit the cycle to one parser post type."
    ),
) -> None:
    """Process a single batch and exit (useful for cron / debugging)."""

    settings = load_settings()
    _configure_logging(settings.log_level)
    _require_targets(settings)

    async def _main() -> list[str]:
        parser_http, maxapi_http, parser, maxapi = _build_clients(settings)
        try:
            return await run_once(
                parser=parser,
                maxapi=maxapi,
                account_id=settings.maxapi_account_id,
                channel_id=settings.maxapi_channel_id,
                worker_id=settings.worker_id,
                lock_ttl_seconds=settings.lock_ttl_seconds,
                batch_size=settings.batch_size,
                post_type=post_type,
            )
        finally:
            await parser_http.aclose()
            await maxapi_http.aclose()

    outcomes = asyncio.run(_main())
    typer.echo(f"processed {len(outcomes)} post(s): {outcomes}")


@app.command("run-loop")
def cmd_run_loop() -> None:
    """Run the publication loop until interrupted (SIGINT / SIGTERM)."""

    settings = load_settings()
    _configure_logging(settings.log_level)
    _require_targets(settings)

    async def _main() -> None:
        parser_http, maxapi_http, parser, maxapi = _build_clients(settings)
        stop_event = asyncio.Event()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                # add_signal_handler is unsupported on some platforms (e.g. Windows).
                pass

        try:
            await run_loop(
                parser=parser,
                maxapi=maxapi,
                account_id=settings.maxapi_account_id,
                channel_id=settings.maxapi_channel_id,
                worker_id=settings.worker_id,
                lock_ttl_seconds=settings.lock_ttl_seconds,
                batch_size=settings.batch_size,
                poll_interval_seconds=settings.poll_interval_seconds,
                stop_event=stop_event,
            )
        finally:
            await parser_http.aclose()
            await maxapi_http.aclose()

    asyncio.run(_main())


@app.command("ping")
def cmd_ping() -> None:
    """Smoke-test connectivity to both upstream services."""

    settings = load_settings()
    _configure_logging(settings.log_level)

    async def _main() -> None:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            parser_resp = await client.get(f"{settings.parser_base_url}/health")
            typer.echo(f"parser  /health -> {parser_resp.status_code} {parser_resp.text.strip()}")
            maxapi_resp = await client.get(f"{settings.maxapi_base_url}/health")
            typer.echo(f"maxapi  /health -> {maxapi_resp.status_code} {maxapi_resp.text.strip()}")

    asyncio.run(_main())


if __name__ == "__main__":
    app()
