"""Low-level page/image fetcher built on the system ``curl`` binary.

Why curl and not httpx/requests: gettyimages.com sits behind CloudFront bot
protection that consistently walls Python HTTP clients (tested: httpx with
HTTP/1.1 and HTTP/2 and exact browser headers, curl_cffi) while the system
curl binary is consistently allowed. curl also picks up the standard proxy
environment (HTTPS_PROXY) and the system CA store with zero configuration,
which keeps this working in managed/proxied environments.
"""

from __future__ import annotations

import subprocess

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class FetchError(RuntimeError):
    def __init__(self, url: str, status: int | None, detail: str = ""):
        self.url = url
        self.status = status
        super().__init__(f"fetch failed ({status}) for {url} {detail}".strip())


def fetch(
    url: str,
    *,
    timeout: int = 40,
    retries: int = 2,
    headers: dict | None = None,
    method: str = "GET",
    data: str | None = None,
) -> bytes:
    """Fetch a URL, returning the body bytes. Raises FetchError on failure."""
    cmd = [
        "curl",
        "--silent",
        "--show-error",
        "--location",
        "--max-time", str(timeout),
        "--user-agent", USER_AGENT,
        "--write-out", "\n%{http_code}",
    ]
    if method != "GET":
        cmd += ["--request", method]
    if data is not None:
        cmd += ["--data", data]
    for key, value in (headers or {}).items():
        cmd += ["--header", f"{key}: {value}"]
    cmd.append(url)

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
        except subprocess.TimeoutExpired as exc:
            last_exc = FetchError(url, None, "timeout")
            continue
        out = proc.stdout
        # split off the trailing status line added by --write-out
        idx = out.rfind(b"\n")
        if idx == -1:
            last_exc = FetchError(url, None, proc.stderr.decode(errors="replace")[:200])
            continue
        body, status_raw = out[:idx], out[idx + 1:]
        try:
            status = int(status_raw.decode().strip() or 0)
        except ValueError:
            status = 0
        if status == 200 and body:
            return body
        last_exc = FetchError(url, status)
    raise last_exc if last_exc else FetchError(url, None)


def fetch_text(url: str, **kwargs) -> str:
    return fetch(url, **kwargs).decode("utf-8", errors="replace")
