"""Interactive command-line launcher for Harmony."""

import subprocess
import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from app.scripts.ui import header

from app.scripts.library_config import PROJECT_ROOT


ACTIONS = {
    "1": ("Scan library", "app.tools.scannerv2"),
    "2": ("Launch player", "app.src.Harmony"),
}


def run_action(choice: str) -> int:
    """Run one supported Harmony command and return its exit code."""
    action = ACTIONS.get(choice)
    if action is None:
        raise ValueError(f"Unknown launcher choice: {choice}")

    label, module = action
    console = Console()
    console.print(f"[cyan]{label}[/cyan]")
    result = subprocess.run(
        [sys.executable, "-m", module],
        cwd=PROJECT_ROOT,
        check=False,
    )
    return result.returncode


def main():
    console = Console()
    console.print(header())

    while True:
        console.print(
            Panel(
                "1. Scan music library\n"
                "2. Launch Harmony player\n"
                "3. Exit",
                title="Harmony Launcher",
                border_style="bright_green",
            )
        )
    
        choice = Prompt.ask(
            "Choose an action",
            choices=("1", "2", "3",),
            default="3",
        )
        if choice == "3":
            return

        console.clear()
        exit_code = run_action(choice)
        console.clear()
        if exit_code != 0:
            console.print(
                f"[bold red]The command exited with status {exit_code}.[/bold red]"
            )


if __name__ == "__main__":
    main()
