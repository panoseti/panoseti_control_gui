# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

`pseti-gui` — a PyQt6 desktop GUI for controlling a PANOSETI observatory (power, DAQ, MAROC/mask config,
calibration) and live-viewing detector images. It is a thin control-panel front end over two sibling repos:
[`panoseti`](../panoseti) (every control button shells out to the `pseti` CLI resolved on `PATH` — see
Architecture) and [`panoseti_grpc`](../panoseti_grpc) (the `AioDaqDataClient` used to stream images, imported
directly as a dependency). `pseti-gui` itself carries no reference to where either sibling repo's source
lives — it only needs `pseti` on `PATH` and, optionally, the host/port of the `panoseti_grpc` server to
stream images from (see Configuration: `grpc_config.json` for the "Start Visualization" button, or CLI args
to `grpc_process.py` for a manual invocation).

## Common Commands

```bash
uv sync                  # install dependencies into .venv
uv run pseti-gui         # run from inside the repo
uv tool install .        # install as a standalone `pseti-gui` command
```

There are no automated tests or CI in this repo — verify changes by running the GUI.

## Configuration

- **`grpc_process.py`** (Typer CLI, see Architecture) takes `--host`/`-o` (default `localhost`) and
  `--port`/`-p` (default `50051`) for the single `panoseti_grpc` server to stream images from. That server
  can be an edge DAQ node (single-machine dev) or a headnode/gateway that fans in multiple edge nodes
  server-side (see [panoseti_grpc's CLAUDE.md](../panoseti_grpc/CLAUDE.md#unified-server) for the
  `role="edge"` vs `role="gateway"` distinction) — `pseti-gui` doesn't need to know or care which; it just
  opens one `AioDaqDataClient(host, port)` and calls `stream_images()`.

- **`start_grpc_clicked()`** in `mainwin.py` (the "Start Visualization" button) gets its `--host`/`--port`
  from `pseti_gui/grpc_config.py`'s `load_grpc_config()`, the same packaged-default-plus-env-var-override
  pattern as `window_config.py`: the `PSETI_GUI_GRPC_CONFIG_FILE` env var if set, otherwise
  `src/pseti_gui/configs/grpc_config.json` (packaged default: `{"host": "localhost", "port": 50051}`). The
  env var is deliberately `PSETI_GUI_*`, not `PSETI_GRPC_*` — that prefix is already used by
  `panoseti_grpc`'s own server-side config env vars (`PSETI_GRPC_DAQ_CONFIG`, `PSETI_GRPC_NETWORK_CONFIG`,
  `PSETI_GRPC_ENV_FILE`), an unrelated package's settings; reusing it here would be exactly the kind of
  confusing collision that got `PSETI_GRPC_DATA_CONFIG` renamed to `PSETI_GRPC_DAQ_CONFIG` in that repo. A
  manual `python -m pseti_gui.grpc_process --host ... --port ...` invocation is unaffected by this file —
  its own `--host`/`--port` flags/defaults are independent of `grpc_config.json`.

- **`window_config.json`** — see [Telescope/module mapping](#telescopemodule-mapping) below.

There used to also be a `configs/panoseti_config.json` (a `verbose` flag) and an older `configs/grpc_config.json`
(dead `daq_config_path`/`net_config_path`/`hp_io_cfg_path` fields from an older multi-node client design) —
both removed; `grpc_stdout`/`grpc_stderr` in `mainwin.py` print unconditionally now. The current
`grpc_config.json` (see above) is a from-scratch reintroduction limited to `host`/`port`, not a restore of
the old file's schema.

**Prerequisite, not a config file:** the `pseti` CLI itself must be installed and resolvable on the `PATH`
of whatever environment launches `pseti-gui` — see Architecture for `uv tool install --editable` and the
`PanoPaths` caveat.

## Architecture

### Two-process, two-IPC-channel design

The GUI process (`MainWin`) never talks gRPC directly. Instead:

1. **Control actions** — every button (`power_on_clicked`, `marocconfig_clicked`, `getuid_clicked`,
   `startdaq_clicked`/`stopdaq_clicked`, redis/reboot/calibration, etc.) goes through `run_pseti(*args)`,
   which invokes the `pseti` CLI **by name on `PATH`** via `QProcess` (`pseti power on`, `pseti cfg
   maroc-config`, `pseti uids`, `pseti start`, `pseti stop`, …) — see the `panoseti/control` CLI reference
   for the full subcommand list. Stdout/stderr stream into the console log pane
   (`ps_stdout`/`ps_stderr`/`append_log`). This deliberately does **not** go through any interpreter path
   configured in `pseti-gui`: `pseti` must be installed as a standalone tool so `pseti-gui` doesn't need to
   know where the `panoseti` checkout or its interpreter live — only that `pseti` resolves on `PATH`.
   **Caveat:** the `control` package's `PanoPaths.software_root_dir()` locates the observatory config/state
   tree from the installed package's own `__file__`, not from an env var by default — a non-editable
   `uv tool install` copies the package into an isolated tool venv, so it would silently resolve configs
   from *that* venv instead of the real checkout. Use `uv tool install --editable <path-to-panoseti>/control`
   (keeps `__file__` pointing at the live checkout) or set `PSETI_ROOT`/`PSETI_CONFIG` in the environment
   `pseti-gui` launches from.
2. **Image streaming** runs in a separate child process (`start_grpc_clicked` launches
   `python -m pseti_gui.grpc_process` via `QProcess`) because `AioDaqDataClient` is asyncio-based and the
   main window runs the Qt event loop. `grpc_process.py` is itself a small Typer CLI (`--host`/`-o`,
   `--port`/`-p`, `--mode`/`-m`); its `DaqDataBackend` owns a single-target `AioDaqDataClient(host, port)`,
   writes each incoming frame into a `multiprocessing.shared_memory.SharedMemory` block, and notifies the
   GUI process over a Unix domain socket at `/tmp/panoseti_meta.sock`. It does **not** call `init_hp_io` —
   it only attaches to a stream that's already running (started by `pseti start` or a server with
   `init_from_default = true`); see
   [panoseti_grpc's CLAUDE.md](../panoseti_grpc/CLAUDE.md#daq-data-service) for who's responsible for
   initialization.

### IPC protocol over the UDS socket

`MainWin` binds `/tmp/panoseti_meta.sock` as a server in `__init__` and registers a `QSocketNotifier` on its
fd (`_on_new_connection` / `_on_ready_read`) — no polling, no threads. `DaqDataBackend` connects as a client
and sends two kinds of newline-delimited JSON messages via `send_metadata()`:

- **Handshake** (`send_shm_info()`): `{"shm": name, "shape": [h, w], "mode": mode}` — the GUI opens the same
  shared-memory block (`create=False`) and wraps it in a same-shape/dtype `np.ndarray` view.
- **Per-frame** (`send_images()` loop): everything from `parsed_pano_image` except `image_array` (which
  lives in shared memory, not JSON) — e.g. `module_id`, `frame_number`. The GUI reads the current shared-memory
  contents into `data['image_array']` and calls `plot_data()`.

Both sides call `resource_tracker.unregister(shm._name, 'shared_memory')` — required because Python's
`resource_tracker` otherwise tries to unlink the block a second time when the *creating* process (`grpc_process`)
exits, racing the GUI process that still holds it open. `stop_grpc_clicked()` in `mainwin.py` is the only place
that actually calls `shm.unlink()`, guarded by `try/except` since the child process may already be gone.

Shutdown is `SIGINT`-driven, not a clean RPC: `stop_grpc_clicked()` sends `SIGINT` to the child's PID
(`self.grpc_process.processId()`), which `grpc_process.py`'s `signal.signal(SIGINT, handler)` turns into
`sys.exit(0)`; `MainWin.closeEvent` calls `stop_grpc_clicked()` if the child is still alive.

### Telescope/module mapping

The image-window grid (dimensions + which `module_id` shows in which cell, with what title) is driven by
`pseti_gui/window_config.py`'s `load_window_config()`, called once in `MainWin.__init__`. Path resolution:
the `PSETI_WINDOW_CONFIG_FILE` env var if set, otherwise the packaged default at
`src/pseti_gui/configs/window_config.json` (today's 2×2 layout: module 250 = PTI, 252 = Fern, 253 = Winter,
254 = Gattini). The file shape is:

```json
{
  "rows": 2,
  "cols": 2,
  "windows": [
    {"module_id": 250, "title": "PTI", "row": 0, "col": 0}
  ]
}
```

`title` is optional per window — an entry that omits it gets the literal string `"None"` as its title
(`DEFAULT_TITLE` in `window_config.py`, also used by `mainwin.py`'s `init_all_plots_zero()` — see
Architecture). `load_window_config()` validates that every `row`/`col` is inside the `rows`×`cols` grid and that no two
windows share a `module_id` or a `(row, col)` position, raising `ValueError` (fail fast at startup) if not.
`MainWin` sizes `static_label`/`plot_widgets`/`timers`/`imgs`/`qttexts` to `rows * cols` and builds a
placeholder in every cell up front (`set_placeholder`/`show_plot` index cells as `row * cols + col`, not a
hardcoded `* 2`). `plot_data()` looks up the incoming frame's `module_id` in `window_config.slots`; a
`module_id` with no entry logs one `warning` (deduped via `_unmapped_module_ids_warned`, not repeated per
frame) and the frame is dropped — it does not derive from `obs_config.json`, there's no `obs_config.json`
path wired into `pseti-gui`. To change the layout (grid size, titles, or which modules are shown), edit
`src/pseti_gui/configs/window_config.json` or point `PSETI_WINDOW_CONFIG_FILE` at a different file — no code
changes needed. `pseti-gui --config-template` (in `app.py`) copies the whole packaged `configs/` directory
(`window_config.json` + `grpc_config.json`) to `./pseti_gui_config_<timestamp>` in the current directory
(`CONFIGS_DIR = Path(__file__).resolve().parent / "configs"`; timestamp via `datetime.now().strftime("%Y%m%d%H%M%S")`,
second-precision) as a starting point to customize and point `PSETI_WINDOW_CONFIG_FILE`/`PSETI_GUI_GRPC_CONFIG_FILE`
at; it refuses to overwrite an existing directory of that exact name rather than clobbering it (`shutil.copytree`,
no `dirs_exist_ok`), and exits without launching the GUI. `app.py`'s `typer.Typer()` also
gets shell-completion (`--install-completion`/`--show-completion`) and `-h` as a `--help` alias for free,
matching `pseti`/`pseti-grpc`'s CLIs — `-h`/`--help` via `context_settings={"help_option_names": [...]}`;
completion via not passing `add_completion=False` (its default is `True`).

`pseti_gui/env_loader.py`'s `load_pseti_gui_env()` (mirroring `panoseti`'s `control.utils.env_loader` and
`panoseti_grpc`'s `util.env_loader`) auto-loads a `.env` file via `python-dotenv` before `app.py`'s module
body does anything else that might read an env var (in particular, before `MainWin.__init__` calls
`load_window_config()`) — a plain `KEY=value` line in `.env` reaches `os.environ` this way, unlike `source`
in a shell, which only sets a shell-local variable a child process never sees. Path resolution: the
`PSETI_GUI_ENV_FILE` env var if set, otherwise `.env` in the current working directory; see
`src/pseti_gui/.env.example` for the variables it's currently useful for (`PSETI_WINDOW_CONFIG_FILE`,
`PSETI_GUI_GRPC_CONFIG_FILE`) — it lives inside the package (not the repo root) so it's still present after
a non-editable install, same reasoning as `configs/`. `pseti-gui --env-template` copies it to
`./.env_gui_<timestamp>` (same timestamped-copy, refuse-to-overwrite pattern as `--config-template`); rename
the copy to `.env` (or point `PSETI_GUI_ENV_FILE` at it) to have it actually loaded.

### UI files: regenerate, don't hand-edit

`src/pseti_gui/mainwin_ui.py` and `data_config_ui.py` are generated from `ui/mainwin.ui` and
`ui/data_config_widget.ui` via `pyuic6` — both files carry a `# WARNING: ... will be lost when pyuic6 is run
again` header. Edit the `.ui` file (Qt Designer) and regenerate, e.g.:

```bash
uv run pyuic6 ui/mainwin.ui -o src/pseti_gui/mainwin_ui.py
uv run pyuic6 ui/data_config_widget.ui -o src/pseti_gui/data_config_ui.py
```

`Ui_MainWindow`/`Ui_Form` are then used as mixins/composition (`MainWin(QMainWindow, Ui_MainWindow)`,
`DataConfigWin.ui = Ui_Form()`) — widget attributes referenced in `mainwin.py`/`data_config_win.py`
(e.g. `self.power_on`, `self.ui.ph_mode_enable`) come from `setupUi()` and only exist after it runs.

### Data config sub-window

`open_data_config()` lazily creates one `DataConfigWin` (`QMainWindow` with a `File > Open...` action,
`self.action_open`) + one `DataConfigOp` (all the get/set logic + `load_config()`/`collect_config()`) and
reuses both on subsequent opens — `DataConfigOp.__init__` wires signals exactly once; don't reconstruct it
per-open, or `on_open_clicked`/`on_ok_clicked`/etc. end up connected multiple times on the same long-lived
`win.ui` widgets and fire once per prior open. There is no hardcoded source file, but `DataConfigOp.__init__`
does try to restore the last one: it reads the last-opened path from `QSettings` (`data_config/last_path`,
persists across app restarts via the org/app name `app.py` sets on the `QApplication`) and calls
`load_config()` on it, best-effort — any failure (file moved/deleted/invalid) just logs a warning and
leaves `src_config` at `None` instead of blocking the window from opening. `on_open_clicked()` (wired to
`action_open.triggered`) pops a `QFileDialog` — pre-seeded with that same remembered path — to pick a
`data_config.json` to load, and writes the chosen path back to `QSettings` for next time. `on_ok_clicked()`
pops a `QMessageBox.question()` confirmation naming the exact `config_output_dir` path before writing
anything (defaults to Cancel) — if that's not the intended file, the dialog itself tells the user to
Cancel and use File > Open... first; only on `Ok` does it call `collect_config()` and close the window.
`collect_config()` writes back to whatever path is currently in the `config_output_dir` line-edit field
(populated by `load_config()`, but also directly user-editable).
`DataConfigOp` is deliberately just get/set pairs per widget plus two translation functions — see
[panoseti's data_config.json constraints](../panoseti/CLAUDE.md#data-config-validation-constraints) for what
values are actually valid before wiring up a new field.

### Console pane: ANSI rendering, wrapping, `--watch` commands, and file mirroring

`MainWin.__init__` builds one `QProcessEnvironment` (`wide_console_env`, shared by `ps_process` and
`grpc_process`) with `FORCE_COLOR=1` — `pseti`'s Rich-based CLI otherwise detects a non-terminal stdout (a
pipe, which is always what `QProcess` gives it) and strips its own ANSI color codes — plus `COLUMNS=200`/
`LINES=50`, since without them Rich falls back to a hardcoded 80-column width and hard-wraps its own output
(splitting table values mid-content) far short of the pane's actual width. `ps_stdout()`/`ps_stderr()` read
that colored text and pass it through `append_log()`, which uses `ansi_html.py`'s `AnsiToHtml` (one
long-lived instance in `MainWin.__init__`, `self._console_html`) to convert the ANSI SGR codes back into an
HTML fragment via Rich's own `AnsiDecoder` + `Console.export_html()`, so the `console_output` pane (a
`QTextEdit`, not `QPlainTextEdit`) renders the same colors/styles a real terminal would. `ansi_html.py` also
remaps Rich's default greens (`#008000`/bright `#00ff00`, the latter from
`panoseti_grpc.telemetry.logger`'s IPv4/IPv6 auto-highlighting) to a calmer, darker green for readability.

Getting this pane to actually *wrap* (rather than grow a horizontal scrollbar) took two fixes working
together, because Rich pads every table-rendered row (timestamps, log levels, ...) out to the full
`COLUMNS=200` width it was just told to assume, which is invisible in a real terminal but would otherwise
show up as either a wall of trailing whitespace or forced horizontal overflow in a GUI pane:
- `ansi_html.py`'s `convert()` strips each line's trailing run of spaces/tabs before returning it, and
  wraps the result in a `<div>`, not a `<pre>` — Qt's rich-text engine never wraps `<pre>` blocks regardless
  of a `white-space` CSS override, which used to force a horizontal scrollbar on every rendered line.
- `MainWin.__init__` additionally calls
  `self.console_output.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)`, since Qt's CSS
  support for `word-break` is unreliable — this wraps at a space when one's available and only falls back
  to breaking mid-token (e.g. a long number) when a single run is wider than the pane itself.

Every line that goes through `append_log()` is additionally mirrored to disk via `gui_log_file.py`'s
`write_console_log_line()` — a no-op unless `PSETI_GUI_LOG_FILE` is set (a path template supporting a
`{date}` placeholder, substituted with the current UTC date on every call so a long-running session rolls
to a new dated file at UTC midnight without a restart); it strips each line's trailing padding the same way
`ansi_html.py` does (the file mirror sees the same Rich-padded raw text) before writing, and writes are
best-effort, swallowing `OSError` so a failure (e.g. an unwritable/missing mount) can never break the
console pane itself.

Command boundaries in the pane are marked by a plain dashed-line separator, printed on both sides of each
boundary rather than deduplicated: `run_pseti()` prints one before the command line it's about to run, and
`ps_finished()` prints another when that command exits successfully (a failed command gets "Command failed"
+ a `^^^^` marker instead) — so two commands run back to back show two separator lines in a row. This is
intentional (each command's own start and end are both marked explicitly), not an oversight.

Some `pseti` subcommands (e.g. `pseti stat --watch`) use Rich's `Live` view, which redraws in place via
ANSI cursor-repositioning escape codes — fine in a real terminal, but garbage when streamed line-by-line
into the console pane the way one-shot commands are. Buttons backing those subcommands instead call
`terminal_launcher.py`'s `open_terminal_with_command()`, which opens a real, detached terminal emulator
outside the GUI process: `osascript`/Terminal.app on macOS, or the first of
`x-terminal-emulator`/`gnome-terminal`/`konsole`/`xfce4-terminal`/`xterm` found on `PATH` on Linux (raises
`RuntimeError` with a manual-run suggestion if none are installed or the platform is neither).

### Image-grid layout widget

The 2×2 (or configured N×M) image grid is laid out by `square_grid.py`'s `SquareGridContainer`, not a plain
`QGridLayout` — a `QGridLayout` stretches each cell to fill its row/column, which is only square when the
container's overall aspect ratio happens to match `rows:cols`. `SquareGridContainer` instead manages child
widget geometry itself on every `resizeEvent`: it picks the largest square cell size that still fits
`cols`×`rows` in the available space, then centers the resulting grid, letterboxing whichever axis doesn't
divide evenly. `MainWin` builds one of these (sized from `window_config.py`'s `rows`/`cols`) and places each
cell's plot widget via `add_widget(widget, row, col)`.

## Logging

Uses the project-standard `panoseti_grpc.telemetry.logger.get_logger(service_name, log_dir=...)` — the same
factory documented in [panoseti's control CLAUDE.md](../panoseti/control/CLAUDE.md#telemetry--logging).
Each component gets its own logger under the `pseti_gui.*` namespace (`pseti_gui.mainwin`,
`pseti_gui.grpc_process`, `pseti_gui.data_config_gen`), writing to `/var/log/panoseti/<hostname>/{service}.log`
+ `.jsonl` plus a Rich console handler; falls back to a temp dir if `/var/log/panoseti` isn't writable by the
current user. `grpc_process.py`'s own stdout/stderr is additionally captured by the *parent* process via
`QProcess` and unconditionally `print()`-ed to the terminal `pseti-gui` was launched from
(`grpc_stdout`/`grpc_stderr` in `mainwin.py`).
