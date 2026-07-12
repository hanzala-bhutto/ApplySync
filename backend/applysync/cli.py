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
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the web dashboard. Pass --reload during development to auto-
    restart on code changes (off by default, matches normal use)."""
    import uvicorn

    uvicorn.run("applysync.web.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
