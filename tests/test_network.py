import asyncio
import socket

import httpcore
import httpx
import pytest
from conftest import mock_client

from romania_mcp import network


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.1", "::1",
                                    "::ffff:127.0.0.1", "224.0.0.1", "0.0.0.0"])
def test_non_public_destinations_are_rejected(address):
    assert not network.public_address(address)


def test_public_ipv4_and_ipv6_are_supported():
    assert network.public_address("8.8.8.8")
    assert network.public_address("2606:4700:4700::1111")


def test_mixed_public_and_private_dns_answers_are_rejected(monkeypatch):
    async def go():
        async def answers(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", answers)
        await network.resolve_public("shop.example", 443)
    with pytest.raises(httpcore.ConnectError):
        asyncio.run(go())


def test_connection_is_pinned_to_the_validated_ip(monkeypatch):
    calls = []
    class Backend:
        async def connect_tcp(self, host, port, **kwargs):
            calls.append((host, port))
            return "connected"
    async def resolve(host, port):
        calls.append((host, port))
        return ["8.8.8.8"]
    monkeypatch.setattr(network, "resolve_public", resolve)
    backend = network.PublicBackend()
    backend.backend = Backend()
    assert asyncio.run(backend.connect_tcp("shop.example", 443, timeout=2)) == "connected"
    assert calls == [("shop.example", 443), ("8.8.8.8", 443)]


def test_unreachable_public_address_falls_back_without_resolving_again(monkeypatch):
    calls = []
    async def resolve(host, port):
        return ["2606:4700:4700::1111", "8.8.8.8"]
    class Backend:
        async def connect_tcp(self, host, port, **kwargs):
            calls.append(host)
            if ":" in host:
                raise httpcore.ConnectError("IPv6 unavailable")
            return "connected"
    monkeypatch.setattr(network, "resolve_public", resolve)
    backend = network.PublicBackend()
    backend.backend = Backend()
    assert asyncio.run(backend.connect_tcp("shop.example", 443, timeout=2)) == "connected"
    assert calls == ["2606:4700:4700::1111", "8.8.8.8"]


def test_dns_failure_is_a_transport_error(monkeypatch):
    async def resolve(*args):
        raise socket.gaierror("unresolvable")
    monkeypatch.setattr(network, "resolve_public", resolve)
    with pytest.raises(httpcore.ConnectError):
        asyncio.run(network.PublicBackend().connect_tcp("missing.example", 443))


def test_redirect_to_private_address_is_blocked_before_connection(monkeypatch):
    connections = []
    async def resolve(host, port):
        if host == "127.0.0.1":
            raise httpcore.ConnectError("Only public internet addresses can be fetched")
        return ["8.8.8.8"]
    class Backend:
        async def connect_tcp(self, host, port, **kwargs):
            connections.append(host)
            return httpcore.AsyncMockStream([
                b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/secret\r\nContent-Length: 0\r\n\r\n"
            ])
    monkeypatch.setattr(network, "resolve_public", resolve)
    async def go():
        transport = network.transport()
        transport._pool._network_backend.backend = Backend()
        async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
            await client.get("http://shop.example")
    with pytest.raises(httpx.ConnectError):
        asyncio.run(go())
    assert connections == ["8.8.8.8"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://shop.ro/a", "https://user:password@shop.ro/"])
def test_web_page_urls_have_no_local_schemes_or_credentials(url):
    async def go():
        async with mock_client(lambda request: httpx.Response(200)) as client:
            return await network.page(client, url)
    with pytest.raises(ValueError):
        asyncio.run(go())


def test_downloaded_page_size_and_content_type_are_bounded():
    async def go():
        async with mock_client(lambda request: httpx.Response(200, text="x" * 100)) as client:
            await network.page(client, "https://shop.ro", max_bytes=10)
    with pytest.raises(ValueError, match="too large"):
        asyncio.run(go())
    async def binary():
        async with mock_client(lambda request: httpx.Response(200, headers={"content-type": "application/pdf"})) as client:
            await network.page(client, "https://shop.ro")
    with pytest.raises(ValueError, match="Expected a web page"):
        asyncio.run(binary())
