"""Observatory status checks (power, transfer daemon, grpc_process) for pseti-gui.

All three functions are synchronous and Qt-free -- two shell out to `pseti`
(resolved on PATH, same as every other control action in this GUI) with
`subprocess.run()` and parse its output; the third scans running processes
with `psutil`. Callers that poll on an interval (see `mainwin.py`) are
responsible for running them off the Qt main thread so a slow/hung `pseti`
invocation or a large process table doesn't freeze the UI.

None of them raise for the underlying check failing outright (`pseti` missing
from PATH, a machine it was never installed on, `psutil` unable to enumerate
processes, ...) -- they fall back to the same "nothing observed" value a real
but empty/negative response would produce, so a caller doesn't need its own
try/except around every poll.
"""

from __future__ import annotations

import os
import subprocess

import psutil

# Rich falls back to an 80-column width when stdout isn't a real terminal and
# COLUMNS/LINES aren't set, which can wrap a long device name/status line
# across two lines and split the substring these checks look for -- widen it
# the same way mainwin.py does for the interactive console pane.
_WIDE_CONSOLE_ENV = dict(os.environ, COLUMNS="200", LINES="50")


def check_power_status(timeout: float = 10.0) -> tuple[int, int]:
    """Run `pseti power` and count devices reporting on vs. total devices.

    Each output line that mentions "power is on" or "power is off" counts as
    one device; any other line (blank lines, log preambles, ...) is ignored.

    Returns:
        (total_count, on_count) -- total devices seen, and how many of those
        reported on. (0, 0) if `pseti power` produced no recognizable lines,
        or if `pseti` itself couldn't be run at all (not on PATH, timed out,
        ...).
    """
    try:
        result = subprocess.run(
            ["pseti", "power"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_WIDE_CONSOLE_ENV,
        )
    except (OSError, subprocess.SubprocessError):
        return 0, 0
    total = 0
    on_count = 0
    for line in result.stdout.splitlines():
        if "power is on" in line:
            total += 1
            on_count += 1
        elif "power is off" in line:
            total += 1
    return total, on_count


def check_transfer_daemon_status(timeout: float = 10.0) -> bool:
    """Run `pseti xfr stat` and report whether the transfer daemon is running.

    Only the first output line is inspected (its "Daemon: RUNNING" /
    "Daemon: NOT RUNNING" state).

    Returns:
        True if the daemon is running, False otherwise (including when
        `pseti xfr stat` produced no output at all, or `pseti` itself
        couldn't be run at all -- not on PATH, timed out, ...).
    """
    try:
        result = subprocess.run(
            ["pseti", "xfr", "stat"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_WIDE_CONSOLE_ENV,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    lines = result.stdout.splitlines()
    if not lines:
        return False
    first_line = lines[0]
    # Check the negative first: "NOT RUNNING" contains "RUNNING" as a
    # substring, so testing for "RUNNING" alone would misreport a stopped
    # daemon as running.
    if "NOT RUNNING" in first_line:
        return False
    return "RUNNING" in first_line


def grpc_process_check() -> bool:
    """Check whether the pseti_gui.grpc_process visualization backend is running.

    Scans running processes for one whose command line invokes
    `pseti_gui.grpc_process` (the module "Start Visualization" launches via
    `<python> -u -m pseti_gui.grpc_process --host ... --port ... -m ...`, see
    mainwin.py's start_grpc_clicked()) -- this looks at the process table
    directly rather than tracking any particular QProcess handle, so it can
    be polled from a plain background thread with no Qt objects involved,
    and reflects the process actually running rather than just this GUI's
    belief about it.

    Returns:
        True if such a process is found, False otherwise (including if
        `psutil` itself fails to enumerate processes for any reason).
    """
    try:
        for proc in psutil.process_iter(["cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if any("pseti_gui.grpc_process" in part for part in cmdline):
                return True
    except Exception:
        return False
    return False
