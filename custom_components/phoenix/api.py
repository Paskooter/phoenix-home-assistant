"""Legacy cloud revocation used only after successful owner-confirmed migration."""

import asyncio

from aiohttp import ClientError, ClientSession
from yarl import URL


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


async def revoke(session: ClientSession, url: str, credential: str) -> bool:
    """Revoke one installation; local removal still works while offline."""
    try:
        url = normalize_url(url)
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
    except ClientError, TimeoutError, PhoenixError:
        return False
