"""Image OCR (ported from alpha `rag/parsers/image_extractor.py` — the
source-agnostic `extract_text_from_image`/`_run_ocr`/`is_meaningful_text`
core; docs/migration/refs/parsers.md §1 "OCR/صور").

`parse_image` OCRs one standalone image file. Cross-image deduplication
(alpha's `seen_hashes` set threaded through a whole batch) needs more than
one image to do anything, so it is not reproduced *inside* this
single-image function; instead `sha1_digest` is exposed as the reusable
building block — a future batch caller (e.g. a PDF's embedded images, when
that lands) builds its own ``seen_hashes`` set by comparing the ``sha1``
this function already stamps into every chunk's metadata.

Risk #2 (parsers.md §6): alpha's own *code* defaults to ``OCR_LANG="eng+ara"``
(its README claims ``"ara+eng"`` — a documented drift). This port picks
**Arabic-first** (``"ara+eng"``), matching the README and this platform's
Arabic-first orientation, as an explicit, documented decision — not a bug
port.

Graceful degradation: `run_ocr` never raises. A missing tesseract binary
(`pytesseract.TesseractNotFoundError`) or any other OCR failure is logged and
returns ``""`` — the platform must keep ingesting a file even when the OCR
binary is absent from a given deployment.

Dropped: alpha's `min_image_width`/`min_image_height`/`min_image_area`
pre-OCR size filter and its "[Diagram/Chart WxHpx]" placeholder-text path —
not in this step's explicit helper list (SHA-1 dedup, `IMAGE_MAX_PIXELS` cap,
upscaling, `is_meaningful_text`, `run_ocr`); every image that decodes is now
sent to OCR and gated purely by `is_meaningful_text` on the resulting text.
"""

from __future__ import annotations

import hashlib
import io

import pytesseract
from PIL import Image

from app.framework.observability import get_logger
from app.modules.knowledge.ports.content_extractor import ParsedChunk, ParsedChunkKind

log = get_logger(__name__)

# Pillow decompression-bomb cap (alpha `IMAGE_MAX_PIXELS`, 64 megapixels) —
# Pillow raises `Image.DecompressionBombError` past this, turned into a
# skip-and-log below (never crash).
IMAGE_MAX_PIXELS = 64 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = IMAGE_MAX_PIXELS

# Arabic-first (risk #2 above) — module constant, not env-tunable.
OCR_LANG = "ara+eng"
_TESSERACT_CONFIG = "--oem 1 --psm 6"  # oem=1 LSTM engine, psm=6 uniform text block.

_OCR_UPSCALE_BELOW_PX = 1000
_OCR_UPSCALE_FACTOR = 2.0
_MIN_MEANINGFUL_TEXT_RATIO = 0.3
# below this length OCR output is noise regardless of alnum/Arabic ratio.
_MIN_MEANINGFUL_TEXT_LEN = 10


def parse_image(data: bytes) -> list[ParsedChunk]:
    """OCR a single standalone image into at most one `ParsedChunk`. Never
    raises: a corrupt/oversized image or a non-meaningful OCR result degrades
    to an empty list (logged), matching alpha's per-image fault tolerance."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            text = run_ocr(img)
    except Image.DecompressionBombError as exc:
        log.warning("image_ocr.decompression_bomb", extra={"error": str(exc)})
        return []
    except Exception as exc:
        log.warning("image_ocr.open_failed", extra={"error": str(exc)})
        return []

    cleaned = text.strip()
    if not is_meaningful_text(cleaned):
        return []

    return [
        ParsedChunk(
            text=cleaned,
            order=0,
            kind=ParsedChunkKind.OCR,
            metadata={"sha1": sha1_digest(data)},
        )
    ]


def run_ocr(img: Image.Image) -> str:
    """Grayscale + upscale-if-small, then tesseract OCR. Never raises — see
    the module docstring."""
    try:
        prepared = _upscale_if_small(img.convert("L"))
    except Exception as exc:
        log.warning("image_ocr.preprocess_failed", extra={"error": str(exc)})
        return ""

    try:
        text: str = pytesseract.image_to_string(prepared, lang=OCR_LANG, config=_TESSERACT_CONFIG)
    except pytesseract.TesseractNotFoundError as exc:
        log.warning("image_ocr.tesseract_not_found", extra={"error": str(exc)})
        return ""
    except Exception as exc:
        log.warning("image_ocr.ocr_failed", extra={"error": str(exc)})
        return ""
    return text.strip()


def is_meaningful_text(text: str, *, min_ratio: float = _MIN_MEANINGFUL_TEXT_RATIO) -> bool:
    """Reject OCR noise/broken characters: at least `min_ratio` of the
    (whitespace-stripped) characters must be alphanumeric OR Arabic (alpha
    `is_meaningful_text`, Arabic range ``؀..ۿ``)."""
    if not text or len(text.strip()) < _MIN_MEANINGFUL_TEXT_LEN:
        return False

    clean = text.strip()
    alnum_count = sum(1 for c in clean if c.isalnum() or "؀" <= c <= "ۿ")
    total_count = len(clean.replace(" ", "").replace("\n", ""))
    if total_count == 0:
        return False
    return (alnum_count / total_count) >= min_ratio


def sha1_digest(data: bytes) -> str:
    """Non-cryptographic content fingerprint for cross-image dedup (alpha's
    `seen_hashes` set) — see the module docstring."""
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def _upscale_if_small(img: Image.Image) -> Image.Image:
    """Upscale a small image so glyphs are large enough for tesseract (alpha
    `_run_ocr` preprocessing)."""
    max_dim = max(img.width, img.height)
    if max_dim < _OCR_UPSCALE_BELOW_PX and _OCR_UPSCALE_FACTOR > 1.0:
        new_size = (int(img.width * _OCR_UPSCALE_FACTOR), int(img.height * _OCR_UPSCALE_FACTOR))
        return img.resize(new_size, Image.Resampling.LANCZOS)
    return img
