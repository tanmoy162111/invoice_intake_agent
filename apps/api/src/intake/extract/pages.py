"""Open uploaded documents and turn them into page images plus text layers.

Untrusted input: everything is bounded (page count, pixel count) and any parse failure becomes
UnreadableFile. Document text is returned to the caller and never logged here.
"""

import io
import math
import struct
from collections.abc import Iterator
from dataclasses import dataclass

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageSequence

PDF = "application/pdf"


class UnreadableFile(Exception):
    """The file could not be opened as a document."""


@dataclass
class RenderedPage:
    image: Image.Image
    text: str
    tone_range: float | None = None  # measured on the first page only


IMAGE_FORMATS = ["PNG", "JPEG", "TIFF"]


def _open_image(content: bytes, max_pixels: int) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = max_pixels
    return Image.open(io.BytesIO(content), formats=IMAGE_FORMATS)


def count_pages(content: bytes, mime: str, *, max_image_pixels: int) -> int:
    try:
        if mime == PDF:
            doc = pdfium.PdfDocument(content)
            try:
                return len(doc)
            finally:
                doc.close()
        with _open_image(content, max_image_pixels) as img:
            if img.width * img.height > max_image_pixels:
                raise UnreadableFile("image has too many pixels")
            return int(getattr(img, "n_frames", 1))
    except UnreadableFile:
        raise
    except Exception as exc:  # corrupt, encrypted, decompression bomb, ...
        raise UnreadableFile(exc.__class__.__name__) from exc


def render_pages(
    content: bytes, mime: str, *, dpi: int, max_pages: int, max_image_pixels: int
) -> Iterator[RenderedPage]:
    """Yield pages one at a time so only one full-size image is in memory. The first page carries
    the tone range. Raises UnreadableFile on any parse failure, or if there are no pages."""
    produced = 0
    try:
        source = (
            _pdf_pages(content, dpi, max_pages, max_image_pixels)
            if mime == PDF
            else _image_pages(content, max_pages, max_image_pixels)
        )
        for page in source:
            if produced == 0:
                page.tone_range = tone_range(page.image)
            produced += 1
            yield page
    except UnreadableFile:
        raise
    except Exception as exc:
        raise UnreadableFile(exc.__class__.__name__) from exc
    if produced == 0:
        raise UnreadableFile("no pages")


def _pdf_pages(content: bytes, dpi: int, max_pages: int, max_pixels: int) -> Iterator[RenderedPage]:
    texts = _pdf_texts(content, max_pages)
    doc = pdfium.PdfDocument(content)
    try:
        for n in range(min(len(doc), max_pages)):
            page = doc[n]
            w, h = page.get_size()
            scale = min(dpi / 72, math.sqrt(max_pixels / max(w * h, 1)))
            image = page.render(scale=scale).to_pil().convert("RGB")
            page.close()
            yield RenderedPage(image, texts[n])
    finally:
        doc.close()


def _pdf_texts(content: bytes, max_pages: int) -> list[str]:
    """Text layer per page. A failure here is not fatal: the page simply has no text layer."""
    texts: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages[:max_pages]:
                try:
                    texts.append(page.extract_text() or "")
                except Exception:
                    texts.append("")
    except Exception:
        pass
    return texts + [""] * (max_pages - len(texts))


def _image_pages(content: bytes, max_pages: int, max_pixels: int) -> Iterator[RenderedPage]:
    with _open_image(content, max_pixels) as img:
        for n, frame in enumerate(ImageSequence.Iterator(img)):
            if n >= max_pages:
                return
            if frame.width * frame.height > max_pixels:  # every frame, not just the first
                raise UnreadableFile("image has too many pixels")
            yield RenderedPage(frame.convert("RGB"), "")


def tone_range(img: Image.Image) -> float:
    """Spread between the 0.5th and 99.5th percentile grey level, measured on a heavily
    downsampled copy so sensor noise averages out. A blank page is close to 0."""
    small = img.convert("L")
    small = small.resize(
        (max(small.width // 8, 1), max(small.height // 8, 1)), Image.Resampling.BOX
    )
    hist = small.histogram()
    total = sum(hist)

    def percentile(p: float) -> int:
        target, acc = total * p, 0
        for level, n in enumerate(hist):
            acc += n
            if acc >= target:
                return level
        return 255

    return float(percentile(0.995) - percentile(0.005))


def fit_for_model(png: bytes, *, max_bytes: int = 7_000_000) -> tuple[bytes, str]:
    """The bytes and media type to send. The API limits an image to 10 MB base64-encoded (about
    7.5 MB raw), so an oversized page is re-encoded as JPEG and shrunk until it fits."""
    if len(png) <= max_bytes:
        return png, "image/png"
    # These are pages we rendered ourselves, so Pillow's bomb guard (a process-wide setting that
    # other code lowers for untrusted uploads) is lifted only for this call.
    previous, Image.MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS, None
    try:
        img = Image.open(io.BytesIO(png)).convert("RGB")
    finally:
        Image.MAX_IMAGE_PIXELS = previous
    for _ in range(8):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        if buf.tell() <= max_bytes:
            return buf.getvalue(), "image/jpeg"
        img = img.resize((int(img.width * 0.8), int(img.height * 0.8)), Image.Resampling.LANCZOS)
    raise UnreadableFile("page image is too large to send")


def png_size(png: bytes) -> tuple[int, int]:
    """Width and height from a PNG header, without decoding (or trusting) the image."""
    if png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        raise UnreadableFile("not a PNG")
    return struct.unpack(">II", png[16:24])
