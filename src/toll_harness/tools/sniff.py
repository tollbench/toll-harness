"""What a file actually IS, read from its own first bytes.

RULE 230 (the typed deliverable, Steven 2026-09-05). A document step's signed
plan names what it hands back: a `channel` (text, file or link), and when the
channel is `file`, a `family` (video, image, audio, document, code) and the
exact `types` under it (``["mp4"]``). The platform sniffs the bytes it is
handed and refuses a file whose real type is not the promised one, so "an HTML
animation renamed .mp4" cannot pass as a video.

WHAT FORCED IT: on production, agent Greg filed three `document` outcomes whose
text sections listed "stan_animation.mp4". No file was ever uploaded; nothing
could tell the difference, because words about a file and a file look the same
in a text section.

This module is the harness's own reading of the same bytes. It is INFORMATIONAL
on the way out -- the platform is authoritative and its 422 is the refusal that
counts -- so a sniffer that has not heard of a container never buries a real
delivery. It is what `files.list` prints beside a size, and what names the part
in the multipart upload.
"""

from __future__ import annotations

# The five families rule 230 names. `family` drives the person's card; `type`
# drives the platform's check.
FAMILIES: tuple[str, ...] = ("video", "image", "audio", "document", "code")

# type -> (family, media type). One row per thing we can recognise from bytes.
_TYPES: dict[str, tuple[str, str]] = {
    "mp4": ("video", "video/mp4"),
    "mov": ("video", "video/quicktime"),
    "webm": ("video", "video/webm"),
    "mkv": ("video", "video/x-matroska"),
    "avi": ("video", "video/x-msvideo"),
    "png": ("image", "image/png"),
    "jpg": ("image", "image/jpeg"),
    "gif": ("image", "image/gif"),
    "webp": ("image", "image/webp"),
    "svg": ("image", "image/svg+xml"),
    "mp3": ("audio", "audio/mpeg"),
    "m4a": ("audio", "audio/mp4"),
    "wav": ("audio", "audio/wav"),
    "flac": ("audio", "audio/flac"),
    "ogg": ("audio", "audio/ogg"),
    "pdf": ("document", "application/pdf"),
    "docx": ("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "xlsx": ("document", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "pptx": (
        "document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
    "zip": ("document", "application/zip"),
    "rtf": ("document", "application/rtf"),
    "xml": ("code", "application/xml"),
    "yaml": ("code", "application/yaml"),
    "html": ("document", "text/html"),
    "json": ("code", "application/json"),
    "csv": ("document", "text/csv"),
    "md": ("document", "text/markdown"),
    "txt": ("document", "text/plain"),
}

UNKNOWN_MEDIA_TYPE = "application/octet-stream"

# Nothing in the bytes separates markdown from a plain note from a python
# file, so a promise of any of these is kept by any other (rule 230, the
# published type table). html, svg and json are positively detected.
PLAIN_TEXT_TYPES: frozenset[str] = frozenset(
    {"txt", "md", "csv", "py", "js", "ts", "sh", "yaml", "rtf", "xml"}
)

# Enough bytes for every signature below, plus room for the EBML DocType and
# the zip's first local file name. Never the whole file: a 50 MB video is
# recognised from its first page.
SNIFF_BYTES = 4096


def _ftyp_brand(head: bytes) -> str:
    return head[8:12].decode("ascii", "ignore").strip().lower()


def _riff(head: bytes) -> str | None:
    form = head[8:12]
    if form == b"WEBP":
        return "webp"
    if form == b"WAVE":
        return "wav"
    if form == b"AVI ":
        return "avi"
    return None


def _zip_type(head: bytes) -> str:
    # A docx/xlsx/pptx is a zip whose first entries name the office part.
    if b"word/" in head:
        return "docx"
    if b"xl/" in head:
        return "xlsx"
    if b"ppt/" in head:
        return "pptx"
    return "zip"


def _text_type(head: bytes) -> str | None:
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" in text:
        return None
    stripped = text.lstrip().lower()
    if stripped.startswith("<!doctype html") or stripped.startswith("<html"):
        return "html"
    if stripped.startswith("<svg") or ("<svg" in stripped[:512] and "xmlns" in stripped[:512]):
        return "svg"
    if stripped[:1] in ("{", "["):
        return "json"
    return "txt"


def sniff_type(head: bytes) -> str | None:
    """The type these bytes really are, or None when nothing here knows.

    Byte signatures only. A filename is a claim; this reads the file.
    """
    if not head:
        return None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith(b"RIFF"):
        found = _riff(head)
        if found:
            return found
    if head[4:8] == b"ftyp":
        brand = _ftyp_brand(head)
        if brand.startswith("qt"):
            return "mov"
        if brand.startswith("m4a"):
            return "m4a"
        return "mp4"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "webm" if b"webm" in head[:256].lower() else "mkv"
    if head.startswith(b"ID3") or (
        len(head) > 1 and head[0] == 0xFF and head[1] in (0xFB, 0xF3, 0xF2, 0xE3)
    ):
        return "mp3"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return _zip_type(head)
    return _text_type(head)


def sniff(head: bytes, *, filename: str | None = None) -> dict[str, str | None]:
    """``{type, family, media_type}`` read from the bytes, never the name.

    `filename` is used for ONE thing and never to name the type: telling a
    plain-text file that is code (``.py``) from one that is prose (``.md``),
    where the bytes genuinely cannot say. An unrecognised file comes back with
    a null type and the octet-stream media type; that is an honest "I do not
    know", not a refusal.
    """
    found = sniff_type(head)
    if found == "txt" and filename:
        suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if suffix in ("md", "markdown"):
            found = "md"
        elif suffix == "csv":
            found = "csv"
        elif suffix in ("py", "js", "ts", "go", "rs", "rb", "java", "c", "h", "cpp", "sh", "sql"):
            return {"type": suffix, "family": "code", "media_type": "text/plain"}
    if found is None:
        return {"type": None, "family": None, "media_type": UNKNOWN_MEDIA_TYPE}
    family, media_type = _TYPES.get(found, (None, UNKNOWN_MEDIA_TYPE))
    return {"type": found, "family": family, "media_type": media_type}


def matches(head: bytes, types: object, *, filename: str | None = None) -> bool | None:
    """Whether these bytes are one of the promised `types`.

    None means "cannot say" -- no promise to check against, or a file this
    module does not recognise. The caller must treat None as PASS: the
    platform is the scanner, and a harness that refused what it merely failed
    to recognise would keep an honest delivery off the person's card.
    """
    promised = [
        str(item).strip().lower().lstrip(".")
        for item in (types if isinstance(types, (list, tuple)) else [])
        if str(item).strip()
    ]
    if not promised:
        return None
    found = sniff(head, filename=filename)
    if not found["type"]:
        return None
    aliases = {"jpeg": "jpg", "htm": "html", "markdown": "md", "text": "txt"}
    normalised = {aliases.get(item, item) for item in promised}
    if found["type"] in normalised:
        return True
    # PLAIN TEXT IS ONE THING TO A SCANNER (agent-skill-appendix, rule 230).
    # Nothing in the bytes separates markdown from a plain note from a python
    # file, so a promise of any plain-text type is kept by any other. html,
    # svg and json are positively detected and never satisfy one.
    if found["type"] in PLAIN_TEXT_TYPES and normalised & PLAIN_TEXT_TYPES:
        return True
    return False
