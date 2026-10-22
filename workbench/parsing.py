"""Process boundary for untrusted PDFs, image decoders, and OCR."""

import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_BYTES = 50 * 1024 * 1024
MAX_PAGES = 200
MAX_PIXELS = 40_000_000
PAGE_TIMEOUT = 90
ERROR_CODES = {
    "unsupported_type",
    "encrypted_pdf",
    "invalid_document",
    "too_many_pages",
    "page_too_large",
    "extraction_limit",
    "parser_timeout",
    "parser_failed",
    "ocr_unavailable",
}


class ParseFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def invoke(source: bytes | bytearray, page: int | None = None) -> tuple[dict, bytes | None]:
    with tempfile.TemporaryDirectory(prefix="workbench-parse-") as directory:
        work = Path(directory)
        (work / "input").write_bytes(source)
        child = subprocess.Popen(
            [sys.executable, "-m", "workbench.parser_child", directory, str(page or 0)],
            env={
                "PATH": os.defpath + ":/usr/local/bin:/opt/homebrew/bin",
                "TMPDIR": directory,
                "OMP_THREAD_LIMIT": "1",
                "LANG": "C.UTF-8",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            child.wait(timeout=PAGE_TIMEOUT if page else 30)
        except subprocess.TimeoutExpired as exc:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            raise ParseFailure("parser_timeout") from exc
        manifest = work / "manifest.json"
        if child.returncode or not manifest.is_file():
            raise ParseFailure("parser_failed")
        data = json.loads(manifest.read_bytes())
        if "error" in data:
            raise ParseFailure(data["error"] if data["error"] in ERROR_CODES else "parser_failed")
        return data, (work / "page.jpg").read_bytes() if page else None
