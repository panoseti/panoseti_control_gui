import shutil
import sys
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

from pseti_gui.env_loader import load_pseti_gui_env
from pseti_gui.mainwin import MainWin

# Load .env before anything below (e.g. MainWin's load_window_config()) reads
# an env var it might set, such as PSETI_WINDOW_CONFIG_FILE.
load_pseti_gui_env()

CONFIGS_DIR = Path(__file__).resolve().parent / "configs"
ENV_EXAMPLE_PATH = Path(__file__).resolve().parent / ".env.example"

app = typer.Typer(
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        print(f"pseti-gui {version('pseti-gui')}")
        raise typer.Exit()


def _config_template_callback(value: bool) -> None:
    if not value:
        return
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    dest = Path.cwd() / f"pseti_gui_config_{timestamp}"
    if dest.exists():
        print(f"Refusing to overwrite existing directory: {dest}")
        raise typer.Exit(code=1)
    shutil.copytree(CONFIGS_DIR, dest)
    print(f"Wrote config template directory to {dest}")
    raise typer.Exit()


def _env_template_callback(value: bool) -> None:
    if not value:
        return
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    dest = Path.cwd() / f".env_gui_{timestamp}"
    if dest.exists():
        print(f"Refusing to overwrite existing file: {dest}")
        raise typer.Exit(code=1)
    shutil.copyfile(ENV_EXAMPLE_PATH, dest)
    print(f"Wrote .env template to {dest}")
    raise typer.Exit()


@app.command()
def main(
    version_opt: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Print the installed pseti-gui version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
    config_template_opt: Annotated[
        bool,
        typer.Option(
            "--config-template",
            help=(
                "Copy the packaged configs/ directory (window_config.json, "
                "grpc_config.json) to ./pseti_gui_config_<timestamp> and exit. "
                "Edit the files inside, then point PSETI_WINDOW_CONFIG_FILE / "
                "PSETI_GUI_GRPC_CONFIG_FILE at them before launching pseti-gui."
            ),
            callback=_config_template_callback,
            is_eager=True,
        ),
    ] = False,
    env_template_opt: Annotated[
        bool,
        typer.Option(
            "--env-template",
            help=(
                "Copy the packaged .env.example to ./.env_gui_<timestamp> and exit. "
                "Point PSETI_GUI_ENV_FILE at the generated file to load it."
            ),
            callback=_env_template_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Launch the PANOSETI Control GUI."""
    qapp = QApplication(sys.argv)
    # Org/app name are required for QSettings() (used e.g. to remember the
    # last-opened data_config.json path) to resolve a persistent store.
    qapp.setOrganizationName("PANOSETI")
    qapp.setApplicationName("pseti-gui")
    icon_path = Path(__file__).resolve().parent / "figure" / "panoseti_icon.png"
    qapp.setWindowIcon(QIcon(str(icon_path)))
    w = MainWin()
    w.setWindowTitle("PANOSETI Control")
    w.show()
    sys.exit(qapp.exec())


if __name__ == "__main__":
    app()
