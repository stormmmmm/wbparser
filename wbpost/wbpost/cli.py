"""``wbpost`` admin CLI — single entry point for ops on top of parser+bridge+maxapi."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from .config import AdminConfig, find_admin_config_path, load_admin_config
from .login import login_and_resolve_channel
from .maxapi_client import MaxApiClient, MaxApiError
from .parser_client import ParserClient, ParserError
from .state import load_state, state_path_from

app = typer.Typer(add_completion=False, help="wbpost — admin CLI for the WB → MAX auto-poster")


def _resolve_config(config_option: Path | None) -> tuple[AdminConfig, Path]:
    path = Path(config_option) if config_option else find_admin_config_path()
    if path is None:
        path = Path("admin.yml")
    cfg = load_admin_config(path)
    return cfg, path


def _state_file(cfg: AdminConfig, config_path: Path) -> Path:
    return state_path_from(config_path, cfg.deployment.state_path)


@app.command("login")
def login_cmd(
    config: Path = typer.Option(None, "--config", "-c", help="Path to admin.yml"),
    force: bool = typer.Option(False, "--force", help="Re-run login even if account already exists"),
) -> None:
    """Авторизовать MAX-аккаунт и найти канал по названию из admin.yml."""
    cfg, cfg_path = _resolve_config(config)
    state_file = _state_file(cfg, cfg_path)
    try:
        outcome = login_and_resolve_channel(
            cfg,
            state_file,
            skip_login_if_account_exists=not force,
        )
    except MaxApiError as exc:
        typer.echo(f"maxapi error: {exc} body={exc.body}", err=True)
        raise typer.Exit(code=2) from exc
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=3) from exc

    typer.echo(
        json.dumps(
            {
                "account_id": outcome.state.account_id,
                "channel_id": outcome.state.channel_id,
                "channel_title": outcome.state.channel_title,
                "reused_existing_account": outcome.reused_existing_account,
                "state_file": str(state_file),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("status")
def status_cmd(
    config: Path = typer.Option(None, "--config", "-c", help="Path to admin.yml"),
) -> None:
    """Показать текущий admin_state.json и здоровье сервисов."""
    cfg, cfg_path = _resolve_config(config)
    state_file = _state_file(cfg, cfg_path)
    state = load_state(state_file)

    payload: dict = {
        "config_path": str(cfg_path),
        "state_file": str(state_file),
        "state": state.model_dump(mode="json") if state else None,
    }

    try:
        with MaxApiClient(cfg.deployment.maxapi_url) as client:
            payload["maxapi_health"] = client.health()
    except Exception as exc:  # noqa: BLE001 - status command is best-effort
        payload["maxapi_health_error"] = str(exc)

    try:
        with ParserClient(cfg.deployment.parser_url) as parser:
            payload["parser_health"] = parser.health()
    except Exception as exc:  # noqa: BLE001
        payload["parser_health_error"] = str(exc)

    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


@app.command("plan-day")
def plan_day_cmd(
    config: Path = typer.Option(None, "--config", "-c", help="Path to admin.yml"),
    date: str = typer.Option(None, "--date", help="YYYY-MM-DD; default = tomorrow"),
) -> None:
    """Расставить planned_at на ready-постах под сетку из admin.yml."""
    cfg, _ = _resolve_config(config)
    slots = [s.model_dump() for s in cfg.schedule.slots]
    with ParserClient(cfg.deployment.parser_url) as parser:
        try:
            result = parser.plan_day(date=date, slots=slots, timezone=cfg.schedule.timezone)
        except ParserError as exc:
            typer.echo(f"parser error: {exc} body={exc.body}", err=True)
            raise typer.Exit(code=2) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))


@app.command("post-once")
def post_once_cmd(
    config: Path = typer.Option(None, "--config", "-c", help="Path to admin.yml"),
    type_: str = typer.Option("collection", "--type", help="single|collection"),
    with_reaction_poll: bool = typer.Option(False, "--with-reaction-poll/--no-with-reaction-poll"),
) -> None:
    """Опубликовать ровно один пост прямо сейчас (для боевого первого пуска)."""
    cfg, _ = _resolve_config(config)
    with ParserClient(cfg.deployment.parser_url) as parser:
        try:
            result = parser.post_once(post_type=type_, with_reaction_poll=with_reaction_poll)
        except ParserError as exc:
            typer.echo(f"parser error: {exc} body={exc.body}", err=True)
            raise typer.Exit(code=2) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))


@app.command("daily-cycle")
def daily_cycle_cmd(
    config: Path = typer.Option(None, "--config", "-c", help="Path to admin.yml"),
) -> None:
    """Запустить полный ежедневный цикл: collect → score → build → plan-day."""
    cfg, _ = _resolve_config(config)
    with ParserClient(cfg.deployment.parser_url) as parser:
        try:
            result = parser.daily_cycle()
        except ParserError as exc:
            typer.echo(f"parser error: {exc} body={exc.body}", err=True)
            raise typer.Exit(code=2) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))


@app.command("show-config")
def show_config_cmd(
    config: Path = typer.Option(None, "--config", "-c", help="Path to admin.yml"),
) -> None:
    """Распечатать текущую разрешённую конфигурацию (с учётом env-overrides)."""
    cfg, cfg_path = _resolve_config(config)
    masked = cfg.model_dump(mode="json")
    if masked.get("max", {}).get("two_factor_password"):
        masked["max"]["two_factor_password"] = "***"
    if masked.get("max", {}).get("phone"):
        digits = "".join(ch for ch in masked["max"]["phone"] if ch.isdigit())
        masked["max"]["phone"] = (
            f"{masked['max']['phone'][:2]}***{digits[-2:]}" if len(digits) >= 4 else "***"
        )
    typer.echo(json.dumps({"path": str(cfg_path), "config": masked}, ensure_ascii=False, indent=2))


def main() -> None:
    sys.exit(app())


if __name__ == "__main__":
    main()
