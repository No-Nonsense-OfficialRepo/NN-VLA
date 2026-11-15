"""Small, best-effort timing log for diagnosing slow room discovery and joins."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile
import threading
import time


_LOCK = threading.Lock()
LOG_PATH = Path(tempfile.gettempdir()) / "lanlauncher-join-timings.log"


def failure(exc: BaseException, prefix: str = "") -> str:
    """Outcome text for a failed phase: exception type plus stage/code when typed.

    Duck-typed (no import of errors.py) so logging can never create an import cycle.
    """
    parts = [f"{prefix}{type(exc).__name__}"]
    stage = getattr(exc, "stage", None)
    code = getattr(exc, "code", None)
    if stage is not None:
        parts.append(f"stage={getattr(stage, 'value', stage)}")
    if code:
        parts.append(f"code={code}")
    return " ".join(parts)


def mark(event: str, started: float | None = None, outcome: str = "") -> None:
    """Append one timing record. Diagnostics must never interrupt the launcher."""
    elapsed = ""
    if started is not None:
        elapsed = f" duration_s={time.monotonic() - started:.3f}"
    safe_outcome = str(outcome).replace("\r", " ").replace("\n", " ")[:200]
    line = (f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds')} "
            f"pid={os.getpid()} event={event}{elapsed} outcome={safe_outcome}\n")
    try:
        with _LOCK, LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
    except OSError:
        pass
