"""Child-process entry point. Set resource bounds before loading decoders."""

import csv
import json
import re
import resource
import shutil
import subprocess
import sys
from contextlib import closing
from pathlib import Path


def set_limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (75, 80))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)


def make_box(left, top, right, bottom, width, height):
    values = tuple(
        max(0.0, min(1.0, value / dimension))
        for value, dimension in (
            (left, width),
            (top, height),
            (right, width),
            (bottom, height),
        )
    )
    if values[0] >= values[2] or values[1] >= values[3]:
        return None
    return dict(zip(("left", "top", "right", "bottom"), values, strict=True))


def ocr(image, path):
    from workbench.parsing import ParseFailure

    if not shutil.which("tesseract"):
        raise ParseFailure("ocr_unavailable")
    image_path = path / "ocr.png"
    image.save(image_path)
    orientation = subprocess.run(
        ["tesseract", str(image_path), "stdout", "--psm", "0"],
        capture_output=True,
        check=False,
        timeout=18,
    )
    rotation = 0
    if orientation.returncode == 0:
        text = orientation.stdout.decode(errors="replace")
        angle = re.search(r"^Rotate: (90|180|270)$", text, re.M)
        confidence = re.search(r"^Orientation confidence: (\d+(?:\.\d+)?)$", text, re.M)
        if angle and confidence and float(confidence[1]) >= 5:
            rotation = int(angle[1])
            image = image.rotate(-rotation, expand=True)
            image.save(image_path)
    output = path / "ocr-result"
    subprocess.run(
        ["tesseract", str(image_path), str(output), "--psm", "11", "-l", "eng", "tsv"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=50,
    )
    words = []
    with (path / "ocr-result.tsv").open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file, delimiter="\t", quoting=csv.QUOTE_NONE):
            if row["level"] != "5" or not row["text"].strip():
                continue
            x, y, w, h = (int(row[key]) for key in ("left", "top", "width", "height"))
            box = make_box(x, y, x + w, y + h, *image.size)
            if box:
                words.append(
                    dict(text=row["text"], box=box, score=max(0, float(row["conf"]) / 100))
                )
    return image, rotation, words


def parse(path: Path, number: int) -> dict:
    import pdfplumber
    import pypdfium2
    import pypdfium2.raw as pdfium
    from pdfminer.pdfdocument import PDFPasswordIncorrect
    from pdfplumber.utils.exceptions import PdfminerException
    from PIL import Image, ImageOps

    from workbench.parsing import MAX_PAGES, MAX_PIXELS, ParseFailure

    source = path / "input"
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with source.open("rb") as file:
        magic = file.read(16)
    pdf = magic.startswith(b"%PDF-")
    supported = ("PNG", "JPEG", "TIFF")
    if not pdf and not magic.startswith(
        (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"II*\0", b"MM\0*")
    ):
        raise ParseFailure("unsupported_type")

    native_words = []
    if pdf:
        try:
            with pdfplumber.open(source) as document:
                if document.doc.encryption is not None:
                    raise ParseFailure("encrypted_pdf")
                count = len(document.pages)
                if not number:
                    return checked_count(count, MAX_PAGES)
                page = document.pages[number - 1]
                # For cropped PDFs use raster OCR so native boxes cannot drift from the image.
                if page.mediabox == page.cropbox and page.rotation == 0:
                    if page.width * page.height > MAX_PIXELS:
                        raise ParseFailure("page_too_large")
                    # A substantial raster can contain transaction cells even when the title is native.
                    image_area = sum(
                        max(0, img["width"]) * max(0, img["height"]) for img in page.images
                    )
                    x0, y0, _, _ = page.bbox
                    words = (
                        page.extract_words() if image_area < 0.12 * page.width * page.height else []
                    )
                    for word in words:
                        box = make_box(
                            word["x0"] - x0,
                            word["top"] - y0,
                            word["x1"] - x0,
                            word["bottom"] - y0,
                            page.width,
                            page.height,
                        )
                        if box:
                            native_words.append(dict(text=word["text"], box=box, score=1.0))
        except PdfminerException as exc:
            code = (
                "encrypted_pdf"
                if isinstance(exc.__context__, PDFPasswordIncorrect)
                else "invalid_document"
            )
            raise ParseFailure(code) from exc
        with pypdfium2.PdfDocument(source) as document:
            with closing(document[number - 1]) as page:
                width, height = page.get_size()
                scale = 180 / 72
                if (
                    width <= 0
                    or height <= 0
                    or width * height * scale**2 > MAX_PIXELS
                    or (max(width, height) * scale > 12000)
                ):
                    raise ParseFailure("page_too_large")
                if native_words and any(
                    pdfium.FPDFTextObj_GetTextRenderMode(obj.raw)
                    in {
                        pdfium.FPDF_TEXTRENDERMODE_INVISIBLE,
                        pdfium.FPDF_TEXTRENDERMODE_CLIP,
                        pdfium.FPDF_TEXTRENDERMODE_UNKNOWN,
                    }
                    for obj in page.get_objects(filter=[pdfium.FPDF_PAGEOBJ_TEXT])
                ):
                    native_words = []
                with closing(page.render(scale=scale, may_draw_forms=False)) as bitmap:
                    image = bitmap.to_pil().convert("RGB")
    else:
        with Image.open(source, formats=supported) as document:
            if not number:
                return checked_count(getattr(document, "n_frames", 1), MAX_PAGES)
            document.seek(number - 1)
            if document.width * document.height > MAX_PIXELS:
                raise ParseFailure("page_too_large")
            image = ImageOps.exif_transpose(document).convert("RGB")
    rotation = 0
    if sum(len(word["text"]) for word in native_words) >= 20:
        source_type, words = "native", native_words
    else:
        source_type = "ocr"
        image, rotation, words = ocr(image, path)
    if len(words) > 5000 or any(len(word["text"]) > 400 for word in words):
        raise ParseFailure("extraction_limit")
    for word in words:
        word["text"] = word["text"].replace("\x00", "�").encode("utf-8", "replace").decode("utf-8")
    image.thumbnail((1400, 1900))
    image.save(path / "page.jpg", quality=86)
    return {
        "number": number,
        "width": image.width,
        "height": image.height,
        "source": source_type,
        "rotation": rotation,
        "words": words,
    }


def checked_count(count: int, limit: int) -> dict:
    from workbench.parsing import ParseFailure

    if count > limit:
        raise ParseFailure("too_many_pages")
    if not count:
        raise ParseFailure("invalid_document")
    return {"pages": count}


def main() -> None:
    set_limits()
    from PIL import Image
    from pypdfium2 import PdfiumError

    from workbench.parsing import ParseFailure

    directory, number = Path(sys.argv[1]), int(sys.argv[2])
    try:
        result = parse(directory, number)
    except ParseFailure as exc:
        result = {"error": exc.code}
    except (subprocess.TimeoutExpired, TimeoutError):
        result = {"error": "parser_timeout"}
    except Image.DecompressionBombError:
        result = {"error": "page_too_large"}
    except subprocess.CalledProcessError:
        result = {"error": "parser_failed"}
    except (PdfiumError, ValueError, OSError, EOFError):
        result = {"error": "invalid_document"}
    (directory / "manifest.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
