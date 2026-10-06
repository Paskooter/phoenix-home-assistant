"""Strict durable request tombstones; never store action content or replay work."""

import asyncio
import json
import os
import stat
from pathlib import Path
from uuid import UUID

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file_atomic

from .const import MAX_SEEN_REQUESTS


def _validated(data) -> dict[str, int]:
    if not isinstance(data, dict) or len(data) > MAX_SEEN_REQUESTS:
        raise ValueError("Invalid request ledger")
    for key, expiry in data.items():
        if not isinstance(key, str):
            raise ValueError("Invalid request ledger")
        request_id = key.split("/", 1)[1] if key.startswith(("announce/", "control/")) else key
        parsed = UUID(request_id)
        if str(parsed) != request_id or parsed.version != 4:
            raise ValueError("Invalid request ledger")
        if not isinstance(expiry, int) or isinstance(expiry, bool) or not 0 <= expiry <= 2**53 - 1:
            raise ValueError("Invalid request ledger")
    return data


class RequestLedger:
    """A fsynced private file whose write errors propagate to admission checks.

    HA's general Store catches write errors after logging them. A request
    tombstone instead has to be confirmed durable before any acknowledgment,
    device service or robot action. Use HA's strict atomic writer directly.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.path = hass.config.path(".storage", f"phoenix.{entry_id}.local_requests")

    def _regular_file(self) -> None:
        try:
            mode = os.lstat(self.path).st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISREG(mode):
            raise ValueError("Request ledger is not a regular file")

    def _load(self) -> dict[str, int]:
        self._regular_file()
        try:
            with open(self.path, "rb") as source:
                raw = source.read(32_769)
        except FileNotFoundError:
            return {}
        if len(raw) > 32_768:
            raise ValueError("Request ledger is too large")
        envelope = json.loads(raw)
        if (
            not isinstance(envelope, dict)
            or type(envelope.get("v")) is not int
            or envelope["v"] != 1
            or set(envelope) != {"v", "requests"}
        ):
            raise ValueError("Invalid request ledger")
        return _validated(envelope["requests"])

    async def async_load(self) -> dict[str, int]:
        return await self.hass.async_add_executor_job(self._load)

    def _save(self, data: dict[str, int]) -> None:
        _validated(data)
        parent = Path(self.path).parent
        parent.mkdir(mode=0o700, exist_ok=True)
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError("Request storage is not a directory")
        self._regular_file()
        serialized = json.dumps({"v": 1, "requests": data}, separators=(",", ":"), allow_nan=False)
        write_utf8_file_atomic(self.path, serialized, private=True)

    async def async_save(self, data: dict[str, int]) -> None:
        # A deadline/cancel must not release the client's admission lock while
        # its executor write can still replace a newer tombstone snapshot.
        pending = self.hass.async_add_executor_job(self._save, dict(data))
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    continue
            pending.result()  # Propagate a failed write even during cancellation.
            raise

    async def async_remove(self) -> None:
        def remove() -> None:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass

        await self.hass.async_add_executor_job(remove)
