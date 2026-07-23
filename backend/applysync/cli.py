from __future__ import annotations

import logging

import typer

from applysync.pipeline.graph import run_sync

app = typer.Typer(help="Job application tracker CLI.")


@app.callback()
def _configure_logging() -> None:
    """Runs once before any subcommand. `serve` also gets this via
    web/app.py's own basicConfig call once uvicorn imports it, but `sync`
    never touches that module, so it needs its own - without this, any
    logger.warning/.exception call anywhere in the pipeline silently went
    nowhere instead of the terminal."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@app.command()
def sync() -> None:
    """Run one pass of the Gmail ingestion + extraction pipeline."""
    stats = run_sync()
    typer.echo(f"run {stats['run_id']}:")
    typer.echo(f"  new emails fetched:    {stats['emails_fetched']}")
    typer.echo(f"  relevant:              {stats['emails_relevant']}")
    typer.echo(f"  applications created:  {stats['applications_created']}")
    typer.echo(f"  status events created: {stats['events_created']}")


@app.command()
def search(query: str, max_results: int = 5) -> None:
    """Query the self-hosted SearXNG instance directly. A smoke test that the
    web-research layer is up before wiring it into the research features."""
    from applysync.config import get_settings
    from applysync.search import SearxngError, get_search_client

    client = get_search_client(get_settings())
    try:
        results = client.search(query, max_results=max_results)
    except SearxngError as exc:
        typer.echo(f"search failed: {exc}", err=True)
        typer.echo("is SearXNG running? (docker compose up -d in searxng/)", err=True)
        raise typer.Exit(code=1) from exc

    if not results:
        typer.echo("no results")
        return
    for i, result in enumerate(results, start=1):
        typer.echo(f"{i}. {result.title}")
        typer.echo(f"   {result.url}")
        if result.content:
            typer.echo(f"   {result.content}")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the web dashboard. Pass --reload during development to auto-
    restart on code changes (off by default, matches normal use)."""
    import uvicorn

    uvicorn.run("applysync.web.app:app", host=host, port=port, reload=reload)


db_app = typer.Typer(help="Database schema migrations (Alembic).")
app.add_typer(db_app, name="db")


def _db_config():
    from applysync.config import get_settings
    from applysync.db.init_db import _alembic_config

    return _alembic_config(get_settings().db_path)


@db_app.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    """Apply migrations up to REVISION (default: head). This is what init_db
    runs on startup; call it directly to migrate without launching the app."""
    from alembic import command

    typer.echo(f"upgrading to {revision} ...")
    command.upgrade(_db_config(), revision)
    typer.echo("done")


@db_app.command("downgrade")
def db_downgrade(revision: str) -> None:
    """Revert migrations down to REVISION (e.g. -1 for one step back)."""
    from alembic import command

    command.downgrade(_db_config(), revision)


@db_app.command("current")
def db_current() -> None:
    """Show the migration revision the database is currently at."""
    from alembic import command

    command.current(_db_config())


@db_app.command("history")
def db_history() -> None:
    """List the migration revisions, newest first."""
    from alembic import command

    command.history(_db_config())


@db_app.command("revision")
def db_revision(message: str, autogenerate: bool = True) -> None:
    """Create a new migration. With --autogenerate (default), Alembic diffs the
    models against the current database and writes the ALTERs for you; review
    the generated file before committing. Use --no-autogenerate for an empty
    migration to hand-write (e.g. a data backfill)."""
    from alembic import command

    command.revision(_db_config(), message=message, autogenerate=autogenerate)


if __name__ == "__main__":
    app()
