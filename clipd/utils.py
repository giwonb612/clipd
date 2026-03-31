from datetime import datetime


def _to_png(image_bytes: bytes) -> bytes:
    """Convert image bytes to PNG. Returns original bytes on failure."""
    # Already PNG — skip conversion
    if image_bytes[:4] == b"\x89PNG":
        return image_bytes
    try:
        from AppKit import NSBitmapImageRep, NSImage
        from Foundation import NSData
        ns_data = NSData.dataWithBytes_length_(image_bytes, len(image_bytes))
        img = NSImage.alloc().initWithData_(ns_data)
        if not img:
            return image_bytes
        tiff = img.TIFFRepresentation()
        rep = NSBitmapImageRep.imageRepWithData_(tiff)
        if not rep:
            return image_bytes
        # NSBitmapImageFileTypePNG = 4
        png = rep.representationUsingType_properties_(4, {})
        return bytes(png) if png else image_bytes
    except Exception:
        return image_bytes


def fmt_time(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    diff = datetime.now() - dt
    if diff.days == 0:
        s = diff.seconds
        if s < 60:
            return f"{s}s ago"
        if s < 3600:
            return f"{s // 60}m ago"
        return f"{s // 3600}h ago"
    if diff.days == 1:
        return "yesterday"
    if diff.days < 7:
        return f"{diff.days}d ago"
    return dt.strftime("%Y-%m-%d")


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f}KB"
    return f"{n / 1024 ** 2:.1f}MB"
