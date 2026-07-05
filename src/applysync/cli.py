from __future__ import annotations

import typer

from applysync.pipeline.graph import run_sync

app = typer.Typer(help="Job application tracker CLI.")


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
def serve() -> None:
    """Run the web dashboard."""
    typer.echo("serve: dashboard not implemented yet (see CLAUDE.md milestone M3)")


if __name__ == "__main__":
    app()
