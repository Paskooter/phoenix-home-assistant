"""Actionable, entry-scoped Repairs without changing pairing or retrying work."""

from collections.abc import Callable

from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN

DISCONNECT_GRACE_SECONDS = 300
TRANSPORT_ISSUES = frozenset(("connection_unavailable", "unsupported_protocol", "invalid_peer", "connection_replaced"))
RECOVERY_ISSUES = TRANSPORT_ISSUES | {"request_storage"}
RECOVERY_GUIDE = (
    "https://github.com/Paskooter/phoenix-home-assistant/blob/main/docs/troubleshooting.md#connection-recovery"
)


class PhoenixRecovery:
    """Observe failures; leave authentication and all execution to the client."""

    def __init__(self, hass, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.active: set[str] = set()
        self._disconnect_cancel: Callable[[], None] | None = None
        self._stopped = False

    @callback
    def _create(self, reason: str) -> None:
        if self._stopped or reason in self.active:
            return
        self.active.add(reason)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{self.entry.entry_id}_{reason}",
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=reason,
            translation_placeholders={"name": self.entry.title},
            learn_more_url=RECOVERY_GUIDE,
        )

    @callback
    def _clear(self, reasons) -> None:
        for reason in reasons:
            self.active.discard(reason)
            ir.async_delete_issue(self.hass, DOMAIN, f"{self.entry.entry_id}_{reason}")

    @callback
    def _cancel_disconnect(self) -> None:
        if self._disconnect_cancel:
            self._disconnect_cancel()
            self._disconnect_cancel = None

    @callback
    def _disconnected(self, _now) -> None:
        self._disconnect_cancel = None
        self._create("connection_unavailable")

    @callback
    def state_changed(self, state: str, error: str | None) -> None:
        if self._stopped:
            return
        if state == "connecting":
            return  # Retry attempts must not reset the original outage deadline.
        if state == "disconnected" and error == "cannot_connect":
            if not self._disconnect_cancel and "connection_unavailable" not in self.active:
                self._disconnect_cancel = async_call_later(self.hass, DISCONNECT_GRACE_SECONDS, self._disconnected)
            return
        self._cancel_disconnect()
        self._clear(TRANSPORT_ISSUES - {error})
        if state == "protocol_error" and error in TRANSPORT_ISSUES:
            self._create(error)
        elif error == "request_storage":
            self.storage_failed()
        # Reauthentication has its own HA flow. A connected socket never proves
        # a failed request ledger became writable again.

    @callback
    def storage_failed(self) -> None:
        self._create("request_storage")

    @callback
    def storage_ready(self) -> None:
        self._clear({"request_storage"})

    @callback
    def stop(self) -> None:
        self._stopped = True
        self._cancel_disconnect()
        self._clear(RECOVERY_ISSUES)
