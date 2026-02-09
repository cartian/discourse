from __future__ import annotations

import sys

import click

from .conversation import Config
from .orchestrator import Orchestrator
from .workshop import WorkshopOrchestrator


@click.command()
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Validate config and exit")
@click.option("--output-dir", type=click.Path(), help="Override output directory")
def main(config_file: str, dry_run: bool, output_dir: str | None) -> None:
    """Run a structured discourse between two Claude sessions.

    CONFIG_FILE is a YAML file defining the topic, participants, and parameters.
    """
    try:
        config = Config.from_yaml(config_file)
    except (ValueError, FileNotFoundError) as e:
        click.echo(f"Error loading config: {e}", err=True)
        sys.exit(1)

    click.echo(f"Config loaded: {config.topic}")
    click.echo(f"  Mode: {config.mode}")

    if config.mode == "workshop":
        click.echo(f"  Author: {config.participants['author'].name}")
        click.echo(f"  Editor: {config.participants['editor'].name}")
        brief_preview = config.brief.strip().split("\n")[0][:80] if config.brief else ""
        click.echo(f"  Brief: {brief_preview}...")
    else:
        click.echo(f"  Participant A: {config.participants['a'].name}")
        click.echo(f"  Participant B: {config.participants['b'].name}")

    click.echo(f"  Max turns: {config.max_turns}")
    click.echo(f"  Check-in interval: {config.check_in_interval}")
    click.echo(f"  Turn timeout: {config.turn_timeout}s")
    click.echo()

    if dry_run:
        click.echo("Dry run — config is valid.")
        return

    if config.mode == "workshop":
        orchestrator = WorkshopOrchestrator(config, output_dir=output_dir)
    else:
        orchestrator = Orchestrator(config, output_dir=output_dir)

    orchestrator.run()


if __name__ == "__main__":
    main()
