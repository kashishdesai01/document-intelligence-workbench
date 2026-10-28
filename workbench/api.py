import asyncio
import csv
import io
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from psycopg import OperationalError
from psycopg_pool import PoolTimeout
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.middleware.gzip import GZipMiddleware

from workbench import service
from workbench.contracts import (
    BankStatement,
    DocumentList,
    DocumentRecord,
    DocumentResult,
    PageMetadata,
    UploadResponse,
    statement_data,
)
from workbench.db import SCHEMA_VERSION, connection_pool, digest_key
from workbench.models import Result, Review
from workbench.parsing import MAX_BYTES
from workbench.service import Actor, Problem


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = connection_pool()
    pool.open()
    app.state.pool = pool
    app.state.upload_slots = asyncio.Semaphore(4)
    try:
        yield
    finally:
        pool.close()


class UploadLimit:
    """Bound multipart parsing even when Content-Length is missing or inaccurate."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        upload = scope["path"] == "/api/v1/documents"
        limit = MAX_BYTES + 64 * 1024 if upload else 8 * 1024
        total = 0

        async def capped_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > limit:
                detail = "workbench_file_too_large" if upload else "workbench_request_too_large"
                raise HTTPException(status_code=413, detail=detail)
            return message

        await self.app(scope, capped_receive, send)


app = FastAPI(title="Document Intelligence Workbench", version="1.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1500)
app.add_middleware(UploadLimit)
bearer = HTTPBearer(auto_error=False)


@app.middleware("http")
async def response_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(Problem)
async def problem_response(_request: Request, error: Problem):
    headers = {"WWW-Authenticate": "Bearer"} if error.status == 401 else None
    return JSONResponse(
        {
            "type": f"urn:workbench:problem:{error.code}",
            "title": error.code.replace("_", " ").title(),
            "status": error.status,
            "code": error.code,
            "detail": error.detail,
        },
        status_code=error.status,
        media_type="application/problem+json",
        headers=headers,
    )


@app.exception_handler(HTTPException)
async def http_problem(request: Request, error: HTTPException):
    if error.status_code == 413 and error.detail == "workbench_file_too_large":
        return await problem_response(
            request, Problem(413, "file_too_large", "The upload exceeds 50 MiB.")
        )
    if error.status_code == 413 and error.detail == "workbench_request_too_large":
        return await problem_response(
            request, Problem(413, "request_too_large", "The request body exceeds 8 KiB.")
        )
    return await problem_response(
        request, Problem(error.status_code, "http_error", str(error.detail))
    )


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, _error: RequestValidationError):
    return await problem_response(request, Problem(422, "invalid_request", "Check request fields."))


@app.exception_handler(PoolTimeout)
@app.exception_handler(OperationalError)
async def unavailable(request: Request, _error: Exception):
    return await problem_response(
        request, Problem(503, "database_unavailable", "Try again shortly.")
    )


def actor_for(
    request: Request,
    auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Actor:
    if auth is None or len(auth.credentials) > 128:
        raise Problem(401, "invalid_api_key", "Provide a valid Bearer API key.")
    digest = digest_key(auth.credentials)
    with request.app.state.pool.connection() as conn:
        row = conn.execute(
            "SELECT org_id FROM api_keys WHERE digest = %s AND revoked_at IS NULL", (digest,)
        ).fetchone()
    if row is None:
        raise Problem(401, "invalid_api_key", "Provide a valid Bearer API key.")
    return Actor(row["org_id"], digest.hex()[:16])


Auth = Annotated[Actor, Depends(actor_for)]


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/readyz")
def ready(request: Request):
    with request.app.state.pool.connection() as conn:
        worker = conn.execute(
            "SELECT 1 FROM worker_heartbeats WHERE seen_at > now() - interval '20 seconds' LIMIT 1"
        ).fetchone()
    if not worker:
        raise Problem(503, "worker_unavailable", "No recent worker heartbeat.")
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/v1/schemas")
def schemas(_actor: Auth):
    return [
        {
            "id": SCHEMA_VERSION,
            "name": "Bank statement",
            "locale": "en-US",
            "currency": "USD",
            "json_schema": BankStatement.model_json_schema(),
        }
    ]


def save_upload(pool, org_id, filename, source, key):
    with pool.connection() as conn:
        return service.upload(conn, org_id, filename, source, key)


@app.post(
    "/api/v1/documents",
    status_code=202,
    response_model=UploadResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "schema_id": {"type": "string", "default": SCHEMA_VERSION},
                        },
                    }
                }
            },
        }
    },
)
async def upload(
    request: Request,
    actor: Auth,
    response: Response,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
):
    from starlette.concurrency import run_in_threadpool

    if idempotency_key is not None and not re.fullmatch(
        r"[\w.:/-]{1,128}", idempotency_key, re.ASCII
    ):
        raise Problem(422, "invalid_idempotency_key", "Use 1–128 ASCII letters, digits, or .:/_-")
    slots = request.app.state.upload_slots
    if slots.locked():
        raise Problem(429, "upload_capacity", "All upload slots are busy. Try again shortly.")
    async with slots, request.form(max_files=1, max_fields=1, max_part_size=1024) as form:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise Problem(422, "missing_file", "Supply one file as multipart field 'file'.")
        if form.get("schema_id", SCHEMA_VERSION) != SCHEMA_VERSION:
            raise Problem(422, "unknown_schema", "Select an available schema.")
        if file.size is not None and file.size > MAX_BYTES:
            raise Problem(413, "file_too_large", "The upload exceeds 50 MiB.")
        source = await file.read(MAX_BYTES + 1)
        if not source:
            raise Problem(422, "empty_upload", "The upload is empty.")
        if len(source) > MAX_BYTES:
            raise Problem(413, "file_too_large", "The upload exceeds 50 MiB.")
        filename = re.sub(
            r"[\x00-\x1f\x7f]", "", (file.filename or "").replace("\\", "/").split("/")[-1]
        )[:160]
        document, reused = await run_in_threadpool(
            save_upload,
            request.app.state.pool,
            actor.org_id,
            filename or "document",
            source,
            idempotency_key,
        )
    response.headers["Location"] = f"/api/v1/documents/{document['id']}"
    return {"document": document, "reused": reused}


@app.get("/api/v1/documents", response_model=DocumentList)
def documents(
    request: Request,
    actor: Auth,
    status: Literal["queued", "processing", "needs_review", "completed", "failed"] | None = None,
    search: Annotated[str | None, Query(max_length=120, pattern=r"^[^\x00]*$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 40,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    order: Literal["newest", "oldest"] = "newest",
):
    if (before_time is None) != (before_id is None):
        raise Problem(422, "invalid_cursor", "Provide both cursor values.")
    clauses = ["org_id = %s"]
    parameters = [actor.org_id]
    if status:
        clauses.append("status = %s")
        parameters.append(status)
    if search:
        clauses.append("strpos(lower(filename), lower(%s)) > 0")
        parameters.append(search)
    direction, comparator = ("ASC", ">") if order == "oldest" else ("DESC", "<")
    if before_time is not None:
        clauses.append(f"(created_at, id) {comparator} (%s, %s)")
        parameters.extend([before_time, before_id])
    where = " AND ".join(clauses)
    with request.app.state.pool.connection() as conn:
        rows = conn.execute(
            f"""SELECT {service.DOCUMENT_FIELDS} FROM documents WHERE {where}
                ORDER BY created_at {direction}, id {direction} LIMIT %s""",
            (*parameters, limit + 1),
        ).fetchall()
    cursor = (
        {"before_time": rows[limit - 1]["created_at"], "before_id": rows[limit - 1]["id"]}
        if len(rows) > limit
        else None
    )
    return {"documents": rows[:limit], "next": cursor}


@app.get("/api/v1/documents/{document_id}", response_model=DocumentRecord)
def document(request: Request, actor: Auth, document_id: UUID):
    with request.app.state.pool.connection() as conn:
        return service.get_document(conn, actor.org_id, document_id)


@app.get("/api/v1/documents/{document_id}/result", response_model=DocumentResult)
def result(request: Request, actor: Auth, document_id: UUID):
    with request.app.state.pool.connection() as conn:
        document = service.get_document(conn, actor.org_id, document_id, shared=True)
        record = service.get_result(conn, actor.org_id, document_id, document)
        return {"document": document, **record["effective"]}


@app.get("/api/v1/documents/{document_id}/pages/{number}", response_model=PageMetadata)
def page(request: Request, actor: Auth, document_id: UUID, number: int):
    with request.app.state.pool.connection() as conn:
        row = conn.execute(
            "SELECT payload - 'words' AS page FROM pages WHERE org_id = %s AND document_id = %s AND number = %s",
            (actor.org_id, document_id, number),
        ).fetchone()
    if not row:
        raise Problem(404, "page_not_found", "Page not found.")
    return row["page"]


@app.get("/api/v1/documents/{document_id}/pages/{number}/image")
def page_image(request: Request, actor: Auth, document_id: UUID, number: int):
    with request.app.state.pool.connection() as conn:
        row = conn.execute(
            "SELECT image FROM pages WHERE org_id = %s AND document_id = %s AND number = %s",
            (actor.org_id, document_id, number),
        ).fetchone()
    if not row:
        raise Problem(404, "page_not_found", "Page not found.")
    return Response(row["image"], media_type="image/jpeg")


@app.post("/api/v1/documents/{document_id}/review/{field_key}", response_model=DocumentResult)
def review(request: Request, actor: Auth, document_id: UUID, field_key: str, action: Review):
    with request.app.state.pool.connection() as conn:
        return service.review(conn, actor, document_id, field_key, action)


@app.get("/api/v1/documents/{document_id}/history")
def history(request: Request, actor: Auth, document_id: UUID, before: int | None = None):
    with request.app.state.pool.connection() as conn:
        service.get_document(conn, actor.org_id, document_id)
        rows = conn.execute(
            """SELECT id, field_key, actor, event, created_at FROM review_events
               WHERE org_id = %s AND document_id = %s AND id < %s
               ORDER BY id DESC LIMIT 51""",
            (actor.org_id, document_id, before if before is not None else 2**63 - 1),
        ).fetchall()
    return {"events": rows[:50], "next": rows[49]["id"] if len(rows) > 50 else None}


@app.post(
    "/api/v1/documents/{document_id}/reprocess", status_code=202, response_model=DocumentRecord
)
def reprocess(request: Request, actor: Auth, document_id: UUID):
    with request.app.state.pool.connection() as conn:
        return service.reprocess(conn, actor.org_id, document_id)


@app.delete("/api/v1/documents/{document_id}", status_code=204)
def delete(request: Request, actor: Auth, document_id: UUID):
    with request.app.state.pool.connection() as conn:
        conn.execute("SELECT id FROM organizations WHERE id = %s FOR UPDATE", (actor.org_id,))
        service.get_document(conn, actor.org_id, document_id, lock=True)
        conn.execute(
            "DELETE FROM documents WHERE org_id = %s AND id = %s", (actor.org_id, document_id)
        )


def csv_safe(value: str) -> str:
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


@app.get("/api/v1/documents/{document_id}/export")
def export(
    request: Request, actor: Auth, document_id: UUID, format: Literal["json", "csv"] = "json"
):
    with request.app.state.pool.connection() as conn:
        doc = service.get_document(conn, actor.org_id, document_id, shared=True)
        if doc["status"] != "completed":
            raise Problem(409, "review_required", "Resolve all review items before exporting.")
        payload = Result.model_validate(
            service.get_result(conn, actor.org_id, document_id, doc)["effective"]
        )
    statement = statement_data(payload)
    headers = {"Content-Disposition": f'attachment; filename="statement-{document_id}.{format}"'}
    if format == "json":
        return JSONResponse(
            {
                "schema": SCHEMA_VERSION,
                "data": statement.model_dump(mode="json"),
                **payload.model_dump(mode="json"),
            },
            headers=headers,
        )
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(("date", "description", "amount", "balance"))
    for row in statement.transactions:
        writer.writerow((row.date, csv_safe(row.description), row.amount, row.balance))
    return Response(buffer.getvalue(), media_type="text/csv", headers=headers)
