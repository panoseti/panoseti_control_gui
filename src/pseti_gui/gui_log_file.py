"""Mirror the console log pane to a file on disk (PSETI_GUI_LOG_FILE).

Mirrors control/legacy/stop.py's "print -> also write to a dated UT log
file" pattern, but appends instead of prepending, and the path is
configurable via an env var template instead of a single hardcoded path.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def resolve_log_file_path() -> Path | None:
    """Return today's console-log mirror file path, or None if unset.

    Reads ``PSETI_GUI_LOG_FILE`` and substitutes ``{date}`` with the current
    UTC date (``YYYYMMDD``) on every call, rather than caching it once, so a
    long-running session rolls over to a new dated file at UTC midnight
    without needing a restart.
    """
    template = os.getenv("PSETI_GUI_LOG_FILE")
    if not template:
        return None
    yyyymmdd = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Path(template.replace("{date}", yyyymmdd))


def write_console_log_line(text: str) -> None:
    """Append *text* (as shown in the console pane) to PSETI_GUI_LOG_FILE.

    No timestamp is added here -- most console-pane lines already carry
    their own (Rich's default local-time prefix on log records; `pseti`'s
    CLI output is similarly self-timestamped where it matters), so *text*
    is written as-is, just stripped of ANSI codes.

    No-op if the env var isn't set. Best-effort: a write failure (e.g. an
    unwritable or missing parent on a remote mount) is swallowed so it can
    never break the console pane itself.
    """
    path = resolve_log_file_path()
    if path is None:
        return
    text = _strip_ansi(text).rstrip("\n")
    if not text:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{text}\n")
    except OSError:
        pass
