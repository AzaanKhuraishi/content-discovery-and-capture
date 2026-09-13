"""Method-appropriate checks. Integrity is not a claim of semantic completeness."""
from pathlib import Path
import json
import struct
import zipfile
from .domain import CaptureError


def verify(path: Path, media_type: str, expected_type: str, role="original"):
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(8192)
    checks = [{"check": "bytes_readable", "result": "PASS", "size": size}]
    limitations = []
    looks_html = b"<html" in head.lower() or b"<!doctype html" in head.lower()
    if looks_html and "html" not in expected_type and expected_type != "application/octet-stream":
        raise CaptureError("Received HTML instead of the expected resource.")
    if "html" in media_type or looks_html:
        if b'type="password"' in head.lower() or b"type='password'" in head.lower():
            raise CaptureError("Captured a sign-in page rather than source content.")
        checks.append({"check": "html_structure", "result": "PASS" if b"<" in head else "UNCERTAIN"})
        limitations += ["Structural inspection does not establish semantic or visual completeness", "Linked assets require their own selected captures"]
    elif "pdf" in media_type or head.startswith(b"%PDF-"):
        if not head.startswith(b"%PDF-"):
            raise CaptureError("Invalid PDF signature.")
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                count = len(pdf.pages)
            checks.append({"check": "pdf_parser", "result": "PASS", "pages": count})
        except ImportError:
            limitations.append("PDF parser unavailable; signature only")
        except Exception:
            raise CaptureError("PDF parser rejected the captured file.") from None
    elif head.startswith(b"PK") or any(x in media_type for x in ("officedocument", "opendocument", "epub", "zip")):
        try:
            with zipfile.ZipFile(path) as archive:
                total = sum(info.file_size for info in archive.infolist())
                if total > 1024**3 or len(archive.infolist()) > 100000:
                    raise CaptureError("Archive verification exceeds its expansion budget.")
                bad = archive.testzip()
                if bad:
                    raise CaptureError("Archive member failed its integrity check.")
                names = archive.namelist()
                required = None
                if "wordprocessingml" in media_type:
                    required = "word/document.xml"
                elif "presentationml" in media_type:
                    required = "ppt/presentation.xml"
                elif "opendocument" in media_type:
                    required = "content.xml"
                elif "epub" in media_type:
                    required = "META-INF/container.xml"
                if required and required not in names:
                    raise CaptureError("Document container lacks its required structure.")
            checks.append({"check": "zip_crc_and_structure", "result": "PASS"})
        except zipfile.BadZipFile:
            raise CaptureError("Invalid document/archive container.") from None
    elif media_type.startswith("image/"):
        try:
            from PIL import Image
            with Image.open(path) as image:
                image.verify()
            checks.append({"check": "image_decoder", "result": "PASS"})
        except ImportError:
            limitations.append("Image decoder unavailable; bytes preserved without visual verification")
        except Exception:
            raise CaptureError("Image decoder rejected captured bytes.") from None
    elif media_type.startswith(("video/", "audio/")):
        try:
            import av
            with av.open(str(path)) as media:
                streams = len(media.streams)
                if not streams:
                    raise ValueError()
                frame = next(media.decode(), None)
                if frame is None:
                    raise ValueError()
            checks.append({"check": "media_stream_and_first_frame", "result": "PASS", "streams": streams})
            limitations.append("Opening and decoding a frame does not verify every media interval")
        except ImportError:
            limitations.append("Media decoder unavailable; container completeness not established")
        except Exception:
            raise CaptureError("Media parser could not decode this resource.") from None
    elif media_type == "application/json":
        try:
            json.loads(path.read_text())
        except (ValueError, UnicodeError):
            raise CaptureError("Invalid JSON output.") from None
        checks.append({"check": "json_parser", "result": "PASS"})
    if not size and role != "original":
        raise CaptureError("Derived output is empty.")
    if not checks[1:] and size:
        limitations.append("Opaque bytes preserved; no format-specific validator was available")
    return {"checks": checks, "status": "VERIFIED_WITH_LIMITATIONS" if limitations else "VERIFIED", "limitations": limitations}
