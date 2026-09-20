# Repository Guidelines

## Project Structure & Architecture

`src/pseti_gui/` contains the Python desktop application. `app.py` provides the Typer entry point; `mainwin.py` coordinates PyQt6 widgets, commands, and image display. `data_config_win.py` handles the data configuration editor. Packaged defaults live in `configs/`, and image assets in `figure/` within the package. Qt Designer sources live in `ui/`; design notes live in `docs/superpowers/`.

Control actions invoke the external `pseti` CLI through `QProcess`. Image streaming runs separately in `grpc_process.py`, using `panoseti_grpc`; pixels reach the GUI through shared memory, while metadata travels over `/tmp/panoseti_meta.sock`. The streaming process subscribes to an already initialized acquisition stream.

## Build, Test, and Development Commands

Python 3.14 or newer is required.

- `uv sync`: install the project and dependencies into `.venv`.
- `uv run pseti-gui`: launch the application locally.
- `uv build`: build wheel and source distributions using Hatchling.
- `uv tool install --editable /path/to/panoseti/control`: install the required `pseti` command while preserving its checkout-based configuration paths.
- `uv run pyuic6 ui/mainwin.ui -o src/pseti_gui/mainwin_ui.py`: regenerate the main window; similarly regenerate `data_config_ui.py` from `ui/data_config_widget.ui`.

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` for functions and modules, and `PascalCase` for classes. Follow surrounding code and use type annotations for new interfaces. No formatter or linter is currently configured. Edit `.ui` sources rather than generated `*_ui.py` files. Keep control operations behind `run_pseti()` and asynchronous streaming in its subprocess.

## Testing Guidelines

There is no automated test suite, configured test framework, or coverage threshold. Validate changes by running the GUI and exercising affected behavior. Check resizing, console output, configuration loading/saving, and visualization startup/shutdown as applicable. Use an appropriate development environment before exercising hardware controls. Record checks and unavailable hardware-dependent validation in the PR.

## Commit & Pull Request Guidelines

Recent commits use prefixes such as `fix:`, `docs:`, and `chore:` followed by concise descriptions. Keep commits focused. PRs should explain the problem, resulting behavior, configuration impacts, and verification performed. Link relevant issues and include screenshots for visible UI changes.

## Configuration

Generate defaults with `pseti-gui --config-template` and `pseti-gui --env-template`. Override paths through `PSETI_WINDOW_CONFIG_FILE`, `PSETI_GUI_GRPC_CONFIG_FILE`, or `PSETI_GUI_ENV_FILE`. Keep local `.env` files and deployment credentials out of commits. Consult `CLAUDE.md` for detailed lifecycle and configuration constraints.
