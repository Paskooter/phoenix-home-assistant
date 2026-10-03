"""TLS-only pairing and revocation. No HA access token leaves Home Assistant."""

import asyncio
from typing import Any
from uuid import UUID

from aiohttp import ClientError, ClientSession
from yarl import URL

from .const import PROTOCOL_VERSION


class PhoenixError(Exception):
    """A safe, translated flow error; never contains credentials or responses."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_url(value: str) -> str:
    """Accept an explicit HTTPS origin, preserving TLS certificate verification."""
    try:
        url = URL(value.strip())
        if (
            url.scheme != "https"
            or not url.host
            or url.user is not None
            or url.password is not None
            or url.path not in ("", "/")
            or url.query_string
            or url.fragment
        ):
            raise ValueError
        return str(url.origin())
    except (ValueError, TypeError, AttributeError) as err:
        raise PhoenixError("invalid_url") from err


async def exchange_code(session: ClientSession, url: str, code: str) -> dict[str, Any]:
    """Exchange a single-use code. Do not retry an uncertain exchange."""
    try:
        async with asyncio.timeout(10):
            async with session.post(
                f"{url}/api/home-assistant/exchange",
                json={"code": code},
                headers={"X-Phoenix-API-Client": "home-assistant"},
                allow_redirects=False,
            ) as response:
                if response.status == 401:
                    raise PhoenixError("invalid_code")
                if response.status == 429:
                    raise PhoenixError("rate_limited")
                if response.status == 409:
                    raise PhoenixError("already_linked")
                if response.status != 200:
                    raise PhoenixError("cannot_connect")
                data = await response.json()
    except (ClientError, TimeoutError, ValueError) as err:
        raise PhoenixError("cannot_connect") from err
    try:
        installation_id = str(UUID(data["installation_id"]))
        credential = data["credential"]
        if (
            data["v"] != PROTOCOL_VERSION
            or not isinstance(credential, str)
            or not credential.startswith(f"{installation_id}.")
            or len(credential) > 200
            or len(credential) < 70
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as err:
        raise PhoenixError("unsupported_protocol") from err
    return {"installation_id": installation_id, "credential": credential}


async def revoke(session: ClientSession, url: str, credential: str) -> bool:
    """Revoke one installation; local removal still works while offline."""
    try:
        async with asyncio.timeout(5):
            async with session.delete(
                f"{url}/api/home-assistant/installation",
                headers={
                    "Authorization": f"Bearer {credential}",
                    "X-Phoenix-API-Client": "home-assistant",
                },
                allow_redirects=False,
            ) as response:
                return response.status in (200, 401, 404)
    except ClientError, TimeoutError:
        return False
