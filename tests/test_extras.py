import base64
import http.server
import os
import struct
import tempfile
import threading
import unittest
import zlib

from minibro.clipboard import dib_to_png
from minibro.fetch import download, is_url
from minibro.store import Store


def decode_png(png):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, size = 8, b"", None
    while pos < len(png):
        n, tag = struct.unpack(">I4s", png[pos:pos + 8])
        data = png[pos + 8:pos + 8 + n]
        assert struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])[0] == zlib.crc32(tag + data)
        if tag == b"IHDR":
            size = struct.unpack(">II", data[:8])
        elif tag == b"IDAT":
            idat += data
        pos += 12 + n
    raw = zlib.decompress(idat)
    w, h = size
    rows = [raw[i * (1 + w * 3) + 1:(i + 1) * (1 + w * 3)] for i in range(h)]
    return w, h, rows


def make_dib(width, height, bpp, pixels_bottom_up):
    """pixels_bottom_up: list of rows (bottom first) of (r, g, b)."""
    step = bpp // 8
    stride = ((width * bpp + 31) // 32) * 4
    body = b""
    for row in pixels_bottom_up:
        line = b"".join(bytes((b, g, r)) + (b"\x00" if step == 4 else b"") for r, g, b in row)
        body += line.ljust(stride, b"\x00")
    hdr = struct.pack("<IiiHHIIiiII", 40, width, height, 1, bpp, 0, len(body), 0, 0, 0, 0)
    return hdr + body


class DibTests(unittest.TestCase):
    def check(self, bpp):
        # bottom row red,green ; top row blue,white
        dib = make_dib(2, 2, bpp, [[(255, 0, 0), (0, 255, 0)], [(0, 0, 255), (255, 255, 255)]])
        w, h, rows = decode_png(dib_to_png(dib))
        self.assertEqual((w, h), (2, 2))
        self.assertEqual(rows[0], bytes([0, 0, 255, 255, 255, 255]))  # top row first
        self.assertEqual(rows[1], bytes([255, 0, 0, 0, 255, 0]))

    def test_24bit(self):
        self.check(24)

    def test_32bit(self):
        self.check(32)

    def test_unsupported_returns_none(self):
        self.assertIsNone(dib_to_png(b"short"))
        bad = struct.pack("<IiiHHIIiiII", 40, 2, 2, 1, 8, 0, 0, 0, 0, 0, 0)
        self.assertIsNone(dib_to_png(bad + b"\x00" * 16))


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"\x89PNG fake"
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class FetchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_is_url(self):
        self.assertTrue(is_url("https://x.com/a.png"))
        self.assertTrue(is_url("data:image/png;base64,AAAA"))
        self.assertFalse(is_url(r"C:\Users\me\a.png"))

    def test_download_name_and_ext(self):
        name, data = download(self.base + "/pics/cat%20one.png?x=1")
        self.assertEqual(name, "cat one.png")
        self.assertEqual(data, b"\x89PNG fake")
        name, _ = download(self.base + "/img")
        self.assertEqual(name, "img.png")

    def test_too_large(self):
        with self.assertRaises(ValueError):
            download(self.base + "/a.png", max_bytes=3)

    def test_data_url(self):
        url = "data:image/png;base64," + base64.b64encode(b"hello").decode()
        self.assertEqual(download(url), ("dropped-image.png", b"hello"))


class AddBytesTest(unittest.TestCase):
    def test_add_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(os.path.join(d, "shelf"))
            item = s.add_bytes("../evil/p.png", b"abc", 60)
            self.assertEqual(item["name"], "p.png")
            self.assertTrue(item["path"].startswith(os.path.join(d, "shelf")))
            with open(item["path"], "rb") as f:
                self.assertEqual(f.read(), b"abc")
            s.remove(item["id"])
            self.assertFalse(os.path.exists(item["path"]))


if __name__ == "__main__":
    unittest.main()
