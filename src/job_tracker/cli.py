from __future__ import annotations

import typer

app = typer.Typer(help="Job application tracker CLI.")


@app.command()
def sync() -> None:
    """Run one pass of the Gmail ingestion + extraction pipeline."""
    typer.echo("sync: pipeline not implemented yet (see CLAUDE.md milestone M2)")


@app.command()
def serve() -> None:
    """Run the web dashboard."""
    typer.echo("serve: dashboard not implemented yet (see CLAUDE.md milestone M3)")


if __name__ == "__main__":
    app()
