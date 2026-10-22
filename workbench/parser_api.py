"""Internal parsing service. Run without database access or credentials."""

import json
import shutil
import struct

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from workbench.parsing import MAX_BYTES, MAX_PAGES, ParseFailure, invoke

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
def health() -> dict:
    if not shutil.which("tesseract"):
        raise HTTPException(503, "OCR is unavailable.")
    return {"status": "ok"}


@app.post("/parse/{number}")
async def parse(request: Request, number: int):
    if not 0 <= number <= MAX_PAGES:
        return JSONResponse({"error": "invalid_document"}, status_code=422)
    source = bytearray()
    async for chunk in request.stream():
        if len(source) + len(chunk) > MAX_BYTES:
            return JSONResponse({"error": "invalid_document"}, status_code=413)
        source.extend(chunk)
    try:
        data, image = await run_in_threadpool(invoke, source, number or None)
    except ParseFailure as exc:
        return JSONResponse({"error": exc.code}, status_code=422)
    if image is None:
        return data
    # Prefix the manifest length to transfer the JSON and JPEG without base64 expansion.
    manifest = json.dumps(data).encode()
    return Response(
        struct.pack(">I", len(manifest)) + manifest + image, media_type="application/octet-stream"
    )
