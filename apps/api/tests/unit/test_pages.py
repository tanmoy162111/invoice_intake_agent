import io
import os

import pytest
from PIL import Image

from intake.extract.pages import UnreadableFile, count_pages, render_pages, tone_range


def tiff(frames: list[tuple[int, int]]) -> bytes:
    imgs = [Image.new("RGB", size, "white") for size in frames]
    buf = io.BytesIO()
    imgs[0].save(buf, "TIFF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def png(size: tuple[int, int] = (50, 50)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "PNG")
    return buf.getvalue()


def test_every_tiff_frame_is_checked_against_the_pixel_limit() -> None:
    data = tiff([(10, 10), (100, 100)])  # second frame is the hostile one
    assert count_pages(data, "image/tiff", max_image_pixels=1000) == 2
    with pytest.raises(UnreadableFile):
        list(render_pages(data, "image/tiff", dpi=100, max_pages=5, max_image_pixels=1000))


def test_first_frame_over_the_limit_is_refused_at_count_time() -> None:
    with pytest.raises(UnreadableFile):
        count_pages(png((100, 100)), "image/png", max_image_pixels=1000)


def test_only_allowed_image_formats_are_opened() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, "BMP")
    with pytest.raises(UnreadableFile):
        count_pages(buf.getvalue(), "image/png", max_image_pixels=10_000)


def test_pages_stream_one_at_a_time_and_only_the_first_has_a_tone_range() -> None:
    pages = render_pages(
        tiff([(20, 20)] * 3), "image/tiff", dpi=100, max_pages=2, max_image_pixels=10_000
    )
    first = next(pages)
    assert first.tone_range is not None
    rest = list(pages)
    assert len(rest) == 1 and rest[0].tone_range is None  # max_pages caps the rest


def test_corrupt_pdf_is_unreadable() -> None:
    with pytest.raises(UnreadableFile):
        list(
            render_pages(
                b"%PDF-1.4 junk", "application/pdf", dpi=100, max_pages=5, max_image_pixels=10**6
            )
        )


def test_tone_range_separates_blank_from_content() -> None:
    blank = Image.new("RGB", (200, 200), (244, 244, 244))
    page = Image.new("RGB", (200, 200), "white")
    for x in range(40, 120):
        for y in range(40, 60):
            page.putpixel((x, y), (0, 0, 0))
    assert tone_range(blank) < 15 < tone_range(page)


def test_fit_for_model_leaves_small_pages_alone() -> None:
    from intake.extract.pages import fit_for_model

    buf = io.BytesIO()
    Image.new("RGB", (200, 200), "white").save(buf, "PNG")
    png = buf.getvalue()
    assert fit_for_model(png) == (png, "image/png")


def test_fit_for_model_shrinks_huge_pages_under_the_limit() -> None:
    from intake.extract.pages import fit_for_model

    noisy = Image.frombytes("RGB", (1800, 1800), os.urandom(1800 * 1800 * 3))
    buf = io.BytesIO()
    noisy.save(buf, "PNG")
    assert len(buf.getvalue()) > 6_000_000
    data, media = fit_for_model(buf.getvalue(), max_bytes=1_000_000)
    assert media == "image/jpeg"
    assert len(data) <= 1_000_000
    assert data[:2] == b"\xff\xd8"  # a JPEG
