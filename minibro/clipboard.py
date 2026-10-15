"""Read files or an image from the Windows clipboard using ctypes only (no Pillow)."""
import struct
import sys
import zlib


def dib_to_png(dib):
    """Convert a clipboard DIB (24/32-bit uncompressed) to PNG bytes, or None if unsupported."""
    if len(dib) < 40:
        return None
    hdr_size, width, height, _planes, bpp, comp = struct.unpack_from("<iiihhi", dib, 0)
    if comp not in (0, 3) or bpp not in (24, 32) or width <= 0 or height == 0:
        return None
    top_down = height < 0
    height = abs(height)
    offset = hdr_size + (12 if comp == 3 and hdr_size == 40 else 0)  # BITFIELDS masks follow a 40-byte header
    stride = ((width * bpp + 31) // 32) * 4
    step = bpp // 8
    if len(dib) < offset + stride * height:
        return None
    raw = bytearray()
    order = range(height) if top_down else range(height - 1, -1, -1)
    for y in order:
        row = dib[offset + y * stride: offset + y * stride + width * step]
        line = bytearray(1 + width * 3)  # leading 0 = PNG filter "none"
        line[1::3] = row[2::step]  # R
        line[2::3] = row[1::step]  # G
        line[3::3] = row[0::step]  # B
        raw += line

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 3))
            + chunk(b"IEND", b""))


def read():
    """Return ("files", [paths]), ("image", png_bytes) or None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32, kernel32, shell32 = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.shell32
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalSize.argtypes = [wintypes.HANDLE]
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]

    CF_DIB, CF_HDROP = 8, 15
    if not user32.OpenClipboard(None):
        return None
    try:
        if user32.IsClipboardFormatAvailable(CF_HDROP):
            h = user32.GetClipboardData(CF_HDROP)
            count = shell32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
            paths = []
            for i in range(count):
                buf = ctypes.create_unicode_buffer(shell32.DragQueryFileW(h, i, None, 0) + 1)
                shell32.DragQueryFileW(h, i, buf, len(buf))
                paths.append(buf.value)
            return ("files", paths) if paths else None
        if user32.IsClipboardFormatAvailable(CF_DIB):
            h = user32.GetClipboardData(CF_DIB)
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                return None
            try:
                dib = ctypes.string_at(ptr, kernel32.GlobalSize(h))
            finally:
                kernel32.GlobalUnlock(h)
            png = dib_to_png(dib)
            return ("image", png) if png else None
        return None
    finally:
        user32.CloseClipboard()
