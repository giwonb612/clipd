import hashlib
from typing import Tuple, Optional


def read_clipboard() -> Tuple[Optional[str], Optional[bytes], Optional[str]]:
    """Returns (type, content_bytes, sha256_hash) or (None, None, None)."""
    try:
        from AppKit import NSPasteboard

        pb = NSPasteboard.generalPasteboard()
        types = pb.types() or []

        # Text — prefer plain UTF-8
        for t in ("public.utf8-plain-text", "NSStringPboardType"):
            if t in types:
                text = pb.stringForType_(t)
                if text:
                    encoded = text.encode("utf-8")
                    return "text", encoded, hashlib.sha256(encoded).hexdigest()

        # Image — prefer PNG, fall back to TIFF
        for t in ("public.png", "public.tiff", "com.adobe.pdf"):
            if t in types:
                data = pb.dataForType_(t)
                if data:
                    raw = bytes(data)
                    return "image", raw, hashlib.sha256(raw).hexdigest()

    except Exception:
        pass

    return None, None, None


def write_clipboard_text(text: str) -> None:
    from AppKit import NSPasteboard

    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, "public.utf8-plain-text")


def write_clipboard_image(data: bytes) -> None:
    from AppKit import NSPasteboard, NSImage
    from Foundation import NSData

    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    ns_data = NSData.dataWithBytes_length_(data, len(data))
    img = NSImage.alloc().initWithData_(ns_data)
    if img:
        pb.writeObjects_([img])
