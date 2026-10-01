import re
import unicodedata

import httpx

USER_AGENT = "romania-mcp/0.1 (+https://github.com/robyroro/romania-mcp)"


def make_client(timeout=20.0):
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )


def describe_error(exc):
    """Short human readable version of whatever went wrong talking to an API."""
    try:
        host = exc.request.url.host
    except (AttributeError, RuntimeError):
        host = "the server"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{host} returned {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return f"timed out talking to {host}, try again"
    if isinstance(exc, httpx.RequestError):
        return f"request to {host} failed: {exc.__class__.__name__}"
    return f"{exc.__class__.__name__}: {exc}"


def fold(text):
    """Lowercase without diacritics, so 'Ştefăneşti', 'Ștefănești' and 'STEFANESTI' compare equal."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def words(text):
    return re.findall(r"[a-z0-9]+", fold(text))
