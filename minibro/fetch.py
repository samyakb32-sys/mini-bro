"""Fetch an image/file dragged out of a browser (http, https or data: URL). stdlib only."""
import base64
import mimetypes
import os
import re
import urllib.parse
import urllib.request

MAX_BYTES = 50 * 1024 * 1024
TIMEOUT = 20


def is_url(text):
    return bool(re.match(r"^(https?://|data:)", text.strip(), re.I))


def _safe_name(name, fallback):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", urllib.parse.unquote(name)).strip(" .")
    return name[:100] or fallback


def _ext(content_type):
    ext = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) or ""
    return {".jpe": ".jpg"}.get(ext, ext)


def download(url, max_bytes=MAX_BYTES):
    """Return (filename, bytes). Raises ValueError/OSError on failure or oversize."""
    url = url.strip()
    if url.lower().startswith("data:"):
        head, _, payload = url[5:].partition(",")
        if len(payload) > max_bytes * 2:
            raise ValueError("too large")
        ctype = head.split(";")[0] or "text/plain"
        data = base64.b64decode(payload) if ";base64" in head else urllib.parse.unquote_to_bytes(payload)
        if len(data) > max_bytes:
            raise ValueError("too large")
        return "dropped-image" + _ext(ctype), data

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MiniBro"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        if int(resp.headers.get("Content-Length") or 0) > max_bytes:
            raise ValueError("too large")
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("too large")
        ctype = resp.headers.get("Content-Type", "")
    name = _safe_name(os.path.basename(urllib.parse.urlparse(url).path), "image")
    if not os.path.splitext(name)[1]:
        name += _ext(ctype)
    return name, data
