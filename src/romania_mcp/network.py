"""Resolve once and connect to the checked address. Redirects use the same transport."""

import asyncio
import ipaddress
import socket
import time

import httpcore
import httpx


def public_address(value):
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


async def resolve_public(host, port):
    loop = asyncio.get_running_loop()
    results = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(r[4][0] for r in results))
    if not addresses or any(not public_address(a) for a in addresses):
        raise httpcore.ConnectError("Only public internet addresses can be fetched")
    return addresses


class PublicBackend(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self.backend = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        # httpcore still holds the original hostname for TLS SNI and certificate validation.
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
            addresses = await asyncio.wait_for(resolve_public(host, port), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise httpcore.ConnectTimeout("Timed out resolving the source hostname") from exc
        except OSError as exc:
            raise httpcore.ConnectError("Could not resolve the source hostname") from exc
        for index, address in enumerate(addresses):
            remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
            try:
                return await self.backend.connect_tcp(
                    address, port, timeout=remaining, local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout):
                if index == len(addresses) - 1:
                    raise

    async def connect_unix_socket(self, *args, **kwargs):
        raise httpcore.ConnectError("Unix sockets are not supported")

    async def sleep(self, seconds):
        await self.backend.sleep(seconds)


def transport():
    result = httpx.AsyncHTTPTransport()
    # HTTPX has no public network_backend argument. Keep this bridge in one place.
    result._pool._network_backend = PublicBackend()
    return result


async def page(client, url, max_bytes=2_000_000, headers=None):
    parsed = httpx.URL(url)
    if parsed.scheme not in ("http", "https") or not parsed.host or parsed.username or parsed.password:
        raise ValueError("Use an http or https URL without login details")
    async with client.stream("GET", parsed, headers=headers) as response:
        response.raise_for_status()
        kind = response.headers.get("content-type", "text/html").split(";", 1)[0].lower()
        if kind not in ("text/html", "application/xhtml+xml", "text/plain"):
            raise ValueError(f"Expected a web page, received {kind}")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > max_bytes:
                raise ValueError("The page is too large to check")
        return str(response.url), body.decode(response.encoding or "utf-8", errors="replace")
