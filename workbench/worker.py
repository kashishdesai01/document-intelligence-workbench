"""Durable page jobs. The lease token fences stale workers after retry or deletion."""

import logging
import signal
import time
from dataclasses import dataclass
from threading import Event, Thread
from uuid import UUID, uuid4

from prometheus_client import Counter, Histogram, start_http_server
from psycopg.types.json import Jsonb

from workbench.db import connection_pool
from workbench.extraction import extract, needs_review, validate
from workbench.models import Page
from workbench.parser_client import parse_page, parser_healthy, preflight
from workbench.parsing import ParseFailure

log = logging.getLogger("workbench.worker")
ATTEMPTS = 3
MAX_DOCUMENT_WORDS = 60_000
MAX_TRANSACTIONS = 2_500
JOB_SECONDS = Histogram("workbench_job_seconds", "Worker attempt duration", ["stage"])
JOB_ERRORS = Counter("workbench_job_errors_total", "Worker attempt failures", ["stage", "code"])


@dataclass(frozen=True)
class Job:
    id: int
    org_id: UUID
    document_id: UUID
    generation: int
    kind: str
    page: int
    attempts: int
    lease: UUID

    @property
    def document(self) -> tuple[UUID, UUID]:
        return self.org_id, self.document_id


def claim(pool) -> Job | None:
    with pool.connection() as conn:
        row = conn.execute(
            """
            WITH next_job AS (
                SELECT j.id
                FROM jobs j JOIN documents d
                  ON (j.org_id, j.document_id, j.generation) = (d.org_id, d.id, d.generation)
                WHERE d.status IN ('queued', 'processing')
                  AND ((j.state = 'pending' AND j.available_at <= now())
                    OR (j.state = 'running' AND j.lease_expires_at <= now()))
                ORDER BY j.available_at, j.id
                FOR UPDATE OF j SKIP LOCKED LIMIT 1
            )
            UPDATE jobs SET state = 'running', lease = %s,
                attempts = attempts + 1, lease_expires_at = now() + interval '150 seconds'
            FROM next_job WHERE jobs.id = next_job.id
            RETURNING jobs.id, org_id, document_id, generation, kind, page, attempts, lease
            """,
            (uuid4(),),
        ).fetchone()
    return Job(**row) if row else None


def run_stage(pool, job: Job):
    with pool.connection() as conn:
        if job.kind == "join":
            count = conn.execute(
                """SELECT sum(jsonb_array_length(payload->'words')) AS total FROM pages
                   WHERE org_id = %s AND document_id = %s""",
                job.document,
            ).fetchone()["total"]
            if count is None or count > MAX_DOCUMENT_WORDS:
                raise ParseFailure("extraction_limit")
            rows = conn.execute(
                "SELECT payload FROM pages WHERE org_id = %s AND document_id = %s ORDER BY number",
                job.document,
            ).fetchall()
        else:
            row = conn.execute(
                "SELECT source FROM uploads WHERE org_id = %s AND document_id = %s", job.document
            ).fetchone()
            if row is None:
                raise ParseFailure("invalid_document")
            source = row["source"]
    if job.kind == "join":
        result = extract([Page.model_validate(row["payload"]) for row in rows])
        if len(result.transactions) > MAX_TRANSACTIONS:
            raise ParseFailure("extraction_limit")
        return result
    if job.kind == "preflight":
        return preflight(source)
    return parse_page(source, job.page)


def lock_current(conn, job: Job) -> dict | None:
    document = conn.execute(
        "SELECT generation, status, page_count FROM documents WHERE org_id = %s AND id = %s FOR UPDATE",
        job.document,
    ).fetchone()
    if (
        not document
        or document["generation"] != job.generation
        or document["status"] not in ("queued", "processing")
    ):
        return None
    lease = conn.execute(
        "SELECT lease, state FROM jobs WHERE id = %s FOR UPDATE", (job.id,)
    ).fetchone()
    return (
        document if lease and lease["state"] == "running" and lease["lease"] == job.lease else None
    )


def finish(pool, job: Job, output) -> None:
    with pool.connection() as conn:
        document = lock_current(conn, job)
        if document is None:
            return
        if job.kind == "preflight":
            conn.execute(
                """UPDATE documents SET status = 'processing', page_count = %s, updated_at = now()
                   WHERE org_id = %s AND id = %s""",
                (output, *job.document),
            )
            conn.execute(
                """INSERT INTO jobs (org_id, document_id, generation, kind, page)
                   SELECT %s, %s, %s, 'page', number FROM generate_series(1, %s) AS number""",
                (*job.document, job.generation, output),
            )
        elif job.kind == "page":
            page, image = output
            conn.execute(
                "INSERT INTO pages VALUES (%s, %s, %s, %s, %s)",
                (*job.document, job.page, image, Jsonb(page.model_dump(mode="json"))),
            )
            complete = conn.execute(
                "SELECT count(*) AS n FROM pages WHERE org_id = %s AND document_id = %s",
                job.document,
            ).fetchone()["n"]
            if complete == document["page_count"]:
                conn.execute(
                    "INSERT INTO jobs (org_id, document_id, generation, kind) VALUES (%s, %s, %s, 'join')",
                    (*job.document, job.generation),
                )
        else:
            row = conn.execute(
                "SELECT decisions FROM results WHERE org_id = %s AND document_id = %s", job.document
            ).fetchone()
            decisions = row["decisions"] if row else {}
            result = validate(output, decisions)
            conn.execute(
                """INSERT INTO results (org_id, document_id, extracted, effective)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (org_id, document_id) DO UPDATE SET
                     extracted = EXCLUDED.extracted, effective = EXCLUDED.effective""",
                (
                    *job.document,
                    Jsonb(output.model_dump(mode="json")),
                    Jsonb(result.model_dump(mode="json")),
                ),
            )
            conn.execute(
                """UPDATE documents SET status = %s, revision = revision + 1, updated_at = now()
                   WHERE org_id = %s AND id = %s""",
                ("needs_review" if needs_review(result) else "completed", *job.document),
            )
        conn.execute("UPDATE jobs SET state = 'done', lease = NULL WHERE id = %s", (job.id,))


def fail(pool, job: Job, code: str, retryable: bool) -> None:
    with pool.connection() as conn:
        if lock_current(conn, job) is None:
            return
        if retryable and job.attempts < ATTEMPTS:
            conn.execute(
                """UPDATE jobs SET state = 'pending', lease = NULL,
                   available_at = now() + make_interval(secs => %s) WHERE id = %s""",
                (2**job.attempts, job.id),
            )
            return
        conn.execute("UPDATE jobs SET state = 'failed', lease = NULL WHERE id = %s", (job.id,))
        conn.execute(
            """UPDATE documents SET status = 'failed', error_code = %s, updated_at = now()
               WHERE org_id = %s AND id = %s""",
            (code, *job.document),
        )


def process_one(pool) -> bool:
    job = claim(pool)
    if job is None:
        return False
    with JOB_SECONDS.labels(job.kind).time():
        try:
            if job.attempts > ATTEMPTS:
                fail(pool, job, "lease_exhausted", False)
            else:
                finish(pool, job, run_stage(pool, job))
        except ParseFailure as exc:
            JOB_ERRORS.labels(job.kind, exc.code).inc()
            fail(pool, job, exc.code, exc.code in {"parser_failed", "parser_timeout"})
        except Exception:
            JOB_ERRORS.labels(job.kind, "internal_processing_error").inc()
            log.error("worker stage failed job=%s stage=%s", job.id, job.kind)
            fail(pool, job, "internal_processing_error", True)
    return True


def heartbeat(pool, worker_id: UUID, stop: Event) -> None:
    while not stop.is_set():
        if not parser_healthy():
            stop.wait(5)
            continue
        try:
            with pool.connection() as conn:
                conn.execute(
                    """INSERT INTO worker_heartbeats VALUES (%s, now())
                       ON CONFLICT (id) DO UPDATE SET seen_at = now()""",
                    (worker_id,),
                )
                conn.execute(
                    "DELETE FROM worker_heartbeats WHERE seen_at < now() - interval '10 minutes'"
                )
        except Exception:
            log.error("worker heartbeat failed")
        stop.wait(5)


def main() -> None:
    import os

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    if port := os.getenv("WORKER_METRICS_PORT"):
        start_http_server(int(port))
    with connection_pool() as pool:
        worker_id = uuid4()
        thread = Thread(target=heartbeat, args=(pool, worker_id, stop), daemon=True)
        thread.start()
        try:
            while not stop.is_set():
                try:
                    if not process_one(pool):
                        stop.wait(0.3)
                except Exception:
                    log.error("worker database operation failed")
                    stop.wait(2)
        finally:
            stop.set()
            thread.join(timeout=6)
            try:
                with pool.connection() as conn:
                    conn.execute("DELETE FROM worker_heartbeats WHERE id = %s", (worker_id,))
            except Exception:
                log.error("worker shutdown heartbeat cleanup failed")
    log.info("worker stopped at %s", time.time())


if __name__ == "__main__":
    main()
