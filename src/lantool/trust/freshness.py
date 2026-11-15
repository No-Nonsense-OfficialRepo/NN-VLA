"""Replay protection for Host-signed data: a signature proves who made it, not that it is current.

The Host stamps every signed game index and game list with an ever-increasing `issued` value. A client remembers
the highest one it has accepted per signer and kind, and rejects anything older, so someone on the LAN cannot hand
it an old (but validly signed) index to roll its files back.

The memory is saved to a small file once the guard is bound to the trusted Host key. Only entries for that key are
kept when it is bound, so a changed Host key starts fresh. Packaged Hosts persist
their signing counter before publishing, maintaining ordering across clock
rollback and restart. Older Hosts without that counter can remain locked out
after clock rollback; clients must never accept older stamps to work around it.
"""
from __future__ import annotations
import json
import os
import threading
from pathlib import Path


class StaleError(ValueError):
    """The signed data is older than data already accepted from the same Host."""


class FreshnessGuard:
    def __init__(self):
        self._seen: dict[tuple[str, str], int] = {}
        self._path: Path | None = None
        self._lock = threading.Lock()

    def bind(self, path, trusted_signer) -> None:
        """Load and persist stamps at `path`, keeping only the entries for `trusted_signer` (None keeps none)."""
        path = Path(path)
        loaded: dict[tuple[str, str], int] = {}
        if trusted_signer:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                entries = value.get("signers", {}).get(str(trusted_signer), {}) if isinstance(value, dict) else {}
                for kind, issued in (entries.items() if isinstance(entries, dict) else []):
                    if isinstance(issued, int) and not isinstance(issued, bool) and issued >= 0:
                        loaded[(str(trusted_signer), str(kind))] = issued
            except (OSError, ValueError, AttributeError):
                loaded = {}
        with self._lock:
            self._path = path
            self._seen = loaded
            self._save_locked()

    def check(self, signer: str, kind: str, obj: dict) -> None:
        """Call after the signature has been verified. Raises StaleError, otherwise records the stamp.

        Data with no `issued` stamp (an older Host version) is accepted only until a stamped one has been seen,
        so a stamped Host cannot be downgraded by replaying its pre-stamp data.
        """
        issued = obj.get("issued")
        key = (str(signer), str(kind))
        with self._lock:
            seen = self._seen.get(key)
            if issued is None:
                if seen is not None:
                    raise StaleError("this Host's data is missing the freshness stamp it used before")
                return
            if not isinstance(issued, int) or isinstance(issued, bool) or issued < 0:
                raise StaleError("the Host's freshness stamp is invalid")
            if seen is not None and issued < seen:
                raise StaleError("this data is older than data already received from the Host")
            if seen != issued:
                self._seen[key] = issued
                self._save_locked()

    def _save_locked(self) -> None:
        if self._path is None:
            return
        signers: dict[str, dict[str, int]] = {}
        for (signer, kind), issued in self._seen.items():
            signers.setdefault(signer, {})[kind] = issued
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(json.dumps({"version": 1, "signers": signers}, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            # Keep the in-memory stamps; the next accepted stamp retries the save.
            pass


# Shared by the library list and file-sync index fetches of this process.
SESSION = FreshnessGuard()
