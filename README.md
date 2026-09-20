# PANOSETI CONTROL GUI
This GUI is based on [panoseti software](https://github.com/panoseti/panoseti) and the [panoseti-grpc](https://pypi.org/project/panoseti-grpc/) package.  
![MAINWIN_GUI](./figure/mainwin_gui.png)  

<img src="./figure/data_config_gui.png" width="400">

# Get Started
1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)
2. **Note:** On Linux, PyQt6 may need an extra system library
    ```
    sudo apt update
    sudo apt install libxcb-cursor0
    ```
3. install the `pseti` CLI (from the [panoseti](https://github.com/panoseti/panoseti) repo's `control/` package) as a
   standalone tool, so it's on your `PATH` — the power/DAQ/config buttons in the GUI shell out to it by name:
    ```
    uv tool install --editable /path/to/panoseti/control
    ```
    Use `--editable` so the installed `pseti` keeps resolving its config/state directories from your actual
    `panoseti` checkout (see [CLAUDE.md](CLAUDE.md) for why).

# Install pseti-gui
There are two ways to install `pseti-gui`; both give you a standalone `pseti-gui` command.

### Option A: install directly from GitHub (recommended, no local clone needed)
```
uv tool install "git+https://github.com/panoseti/panoseti_control_gui.git"
pseti-gui
```
`panoseti-grpc`, the other in-house dependency, is resolved from [PyPI](https://pypi.org/project/panoseti-grpc/),
so this doesn't need a local checkout of anything except the `panoseti/control` package (previous step).
To pin a specific released version instead of the latest commit on the default branch, append `@<tag>`:
```
uv tool install "git+https://github.com/panoseti/panoseti_control_gui.git@v0.3.0"
```
To upgrade later, re-run the same command with `--reinstall`.

### Option B: install from a local clone (for development)
```
git clone https://github.com/liuweiseu/panoseti_control_gui.git
cd panoseti_control_gui
uv sync
uv run pseti-gui        # run inside the repo without installing, or:
uv tool install --editable .   # install as a standalone command that tracks your local edits
```

# Configuration
Generate a starting point for both config files at once:
```
pseti-gui --config-template   # writes ./pseti_gui_config_<timestamp>/{window_config,grpc_config}.json
```
1. (optional) customize the image-window grid  
    `pseti-gui` shows a fixed 2x2 grid (PTI/Fern/Winter/Gattini) out of the box, sized to fill the display
    area with square cells. To change the grid size or which module shows where, edit
    `window_config.json` in the generated directory, then point `pseti-gui` at it:
    ```
    export PSETI_WINDOW_CONFIG_FILE=/path/to/pseti_gui_config_<timestamp>/window_config.json
    ```
2. (optional) point the image-streaming backend at a non-default `panoseti_grpc` server  
    The "Start Visualization" button connects to `localhost:50051` by default. To point it elsewhere — an
    edge DAQ node, or a headnode/gateway that fans in multiple edge nodes server-side — edit
    `grpc_config.json`'s `host`/`port` in the generated directory:
    ```
    {"host": "<host>", "port": <port>}
    ```
    then point `pseti-gui` at it:
    ```
    export PSETI_GUI_GRPC_CONFIG_FILE=/path/to/pseti_gui_config_<timestamp>/grpc_config.json
    ```
    Alternatively, run `python -m pseti_gui.grpc_process --host <host> --port <port> --mode <mode>` directly
    instead of using the button (`--mode` is one of `mov8`/`mov16`/`ph256`/`ph1024`, default `ph1024`) —
    this bypasses `grpc_config.json` entirely.
    The stream must already be initialized (e.g. via `pseti start`, or a server with
    `init_from_default = true`) — this GUI only attaches to it.

# Start GUI
Run `pseti-gui -h` (or `--help`) to see all CLI options, including `--version`, `--config-template` (see
Configuration above), `--env-template` (writes `./.env_gui_<timestamp>` — rename it to `.env`, or point
`PSETI_GUI_ENV_FILE` at it, to have `pseti-gui` load it automatically), and shell completion
(`--install-completion`/`--show-completion`, same as `pseti`/`pseti-grpc`).

Once the image-streaming backend ("Start Visualization" button) is running, its stdout/stderr are printed
directly to the terminal `pseti-gui` was launched from; every window shows a zero-valued image until its
module's first real frame arrives, and reverts to the default placeholder image on "Stop Visualization".

The Data Config window (Configs > Data Config) remembers the last file you opened across GUI restarts, and
asks for confirmation naming the exact target file before writing it.

# Dashboard controls

The main window sizes the camera/control split from the available image height, with five command groups, a colored
console with a Clear button, and a bottom status bar. Camera slots and titles still
come from `window_config.json`; the default remains the four configured modules.
The five command groups always stay in one horizontal row. Their full width is
reserved before allocating space to the images; narrower windows use smaller square
camera cells. The image header reads Telescope View. The clock is centered in the bottom status bar, beside the status indicators; power controls are right-aligned
in the control header. Drag the divider between the camera and control panels to
adjust their widths. The initial layout fits square images; after a manual adjustment,
resizing the window preserves the chosen split subject to panel minimum widths.
The default 1640×900 window fits a 1920×1080 display;
command panels use compact, content-aware widths instead of stretching. Visualization
buttons are labeled Start Visual / Stop Visual.

- **Power:** one switch runs the existing `pseti power on/off` commands. It is disabled
  while the command runs and restored on failure. `ON*`/`OFF*` report the last successful
  command, not measured hardware status; the initial status is Unknown.
- **Initialization:** Validate, Health, Reboot, and GetUID. Redis controls have been removed.
- **DAQ:** Start/Stop keep their existing commands. Start Interleave currently logs a
  placeholder message; its command will be connected later.
- **Visualization:** choose PH1024, MOVIE16, or MOVIE8 before starting. The selection
  supplies `ph1024`, `mov16`, or `mov8` to `grpc_process`; movie modes subscribe to the
  movie stream. Stop visualization before changing modes. PH512 is not included yet.
- **Status lights:** Cameras, DAQ, Visualization, and Transfer initially show Unknown.
  Monitoring is not implemented. Future observers can call
  `MainWin.set_subsystem_status(subsystem, state, detail)` on the GUI thread, using
  `unknown`, `idle`, `running`, `warning`, or `error` states.

Layout changes belong in `ui/mainwin.ui`; switch, status, and styling components live
in `src/pseti_gui/dashboard_widgets.py`. Regenerate the UI with
`uv run python -m PyQt6.uic.pyuic ui/mainwin.ui -o src/pseti_gui/mainwin_ui.py`.
