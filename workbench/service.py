"""Tenant-scoped transactions used by the HTTP layer."""

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from workbench.db import PIPELINE_VERSION, SCHEMA_VERSION
from workbench.extraction import needs_review, signature, validate
from workbench.grounding import normalize
from workbench.models import Evidence, Result, Review


class Problem(Exception):
    def __init__(self, status: int, code: str, detail: str):
        self.status, self.code, self.detail = status, code, detail
        super().__init__(detail)


@dataclass(frozen=True)
class Actor:
    org_id: UUID
    key_id: str


DOCUMENT_FIELDS = """
    id, filename, schema_version, pipeline_version, status, error_code, generation,
    revision, page_count, created_at, updated_at
"""
ACTIVE = {"queued", "processing"}


def get_document(conn, org_id, document_id, *, lock=False, shared=False) -> dict:
    sql = f"SELECT {DOCUMENT_FIELDS} FROM documents WHERE org_id = %s AND id = %s"
    if lock:
        sql += " FOR UPDATE"
    elif shared:
        sql += " FOR SHARE"
    row = conn.execute(sql, (org_id, document_id)).fetchone()
    if row is None:
        raise Problem(404, "document_not_found", "Document not found.")
    return row


def enqueue(conn, org_id, document_id, generation: int) -> None:
    conn.execute(
        "INSERT INTO jobs (org_id, document_id, generation, kind) VALUES (%s, %s, %s, 'preflight')",
        (org_id, document_id, generation),
    )


def upload(
    conn, org_id: UUID, filename: str, source: bytes, token: str | None
) -> tuple[dict, bool]:
    digest = hashlib.sha256(source).digest()
    # Serialize admission per organization, including concurrent idempotent requests.
    conn.execute("SELECT id FROM organizations WHERE id = %s FOR UPDATE", (org_id,))
    if token is not None:
        record = conn.execute(
            "SELECT sha256, document_id FROM idempotency_keys WHERE org_id = %s AND key = %s",
            (org_id, token),
        ).fetchone()
        if record:
            if record["sha256"] != digest:
                raise Problem(
                    409, "idempotency_conflict", "This key was used for a different upload."
                )
            return get_document(conn, org_id, record["document_id"]), True
    row = conn.execute(
        """SELECT id FROM documents
           WHERE org_id = %s AND sha256 = %s AND schema_version = %s AND pipeline_version = %s""",
        (org_id, digest, SCHEMA_VERSION, PIPELINE_VERSION),
    ).fetchone()
    reused = row is not None
    if row is None:
        active = conn.execute(
            """SELECT count(*) AS n FROM documents
               WHERE org_id = %s AND status IN ('queued', 'processing')""",
            (org_id,),
        ).fetchone()["n"]
        if active >= 20:
            raise Problem(429, "upload_limit", "Try again after existing jobs finish.")
        limit = conn.execute(
            """INSERT INTO upload_windows VALUES (%s, now(), 1)
               ON CONFLICT (org_id) DO UPDATE SET
                   uploads = CASE WHEN upload_windows.started_at <= now() - interval '1 minute'
                                  THEN 1 ELSE upload_windows.uploads + 1 END,
                   started_at = CASE WHEN upload_windows.started_at <= now() - interval '1 minute'
                                      THEN now() ELSE upload_windows.started_at END
               RETURNING uploads""",
            (org_id,),
        ).fetchone()["uploads"]
        if limit > 15:
            raise Problem(429, "upload_limit", "Too many uploads. Try again in a minute.")
        document_id = uuid4()
        conn.execute(
            """INSERT INTO documents (org_id, id, filename, sha256, schema_version, pipeline_version, status)
               VALUES (%s, %s, %s, %s, %s, %s, 'queued')""",
            (org_id, document_id, filename, digest, SCHEMA_VERSION, PIPELINE_VERSION),
        )
        conn.execute("INSERT INTO uploads VALUES (%s, %s, %s)", (org_id, document_id, source))
        enqueue(conn, org_id, document_id, 1)
    else:
        document_id = row["id"]
    if token is not None:
        conn.execute(
            "INSERT INTO idempotency_keys VALUES (%s, %s, %s, %s)",
            (org_id, token, document_id, digest),
        )
    return get_document(conn, org_id, document_id), reused


def reprocess(conn, org_id, document_id) -> dict:
    conn.execute("SELECT id FROM organizations WHERE id = %s FOR UPDATE", (org_id,))
    document = get_document(conn, org_id, document_id, lock=True)
    if document["status"] in ACTIVE:
        raise Problem(409, "processing_active", "Processing is already active.")
    active = conn.execute(
        """SELECT count(*) AS n FROM documents WHERE org_id = %s AND status IN ('queued', 'processing')""",
        (org_id,),
    ).fetchone()["n"]
    if active >= 20:
        raise Problem(429, "upload_limit", "Try again after existing jobs finish.")
    conn.execute("DELETE FROM pages WHERE org_id = %s AND document_id = %s", (org_id, document_id))
    conn.execute("DELETE FROM jobs WHERE org_id = %s AND document_id = %s", (org_id, document_id))
    conn.execute(
        """UPDATE documents SET status = 'queued', generation = generation + 1,
           revision = revision + 1, error_code = NULL, page_count = NULL, updated_at = now()
           WHERE org_id = %s AND id = %s""",
        (org_id, document_id),
    )
    enqueue(conn, org_id, document_id, document["generation"] + 1)
    return get_document(conn, org_id, document_id)


def get_result(conn, org_id, document_id, document=None) -> dict:
    document = document or get_document(conn, org_id, document_id)
    if document["status"] not in {"completed", "needs_review"}:
        raise Problem(409, "result_unavailable", "There is no current result for this document.")
    return conn.execute(
        "SELECT extracted, effective, decisions FROM results WHERE org_id = %s AND document_id = %s",
        (org_id, document_id),
    ).fetchone()


def review(conn, actor: Actor, document_id, field_key: str, action: Review) -> dict:
    org_id = actor.org_id
    document = get_document(conn, org_id, document_id, lock=True)
    if document["revision"] != action.revision:
        raise Problem(409, "stale_revision", "The document changed. Reload it before reviewing.")
    record = get_result(conn, org_id, document_id, document)
    raw = Result.model_validate(record["extracted"])
    before = next(
        (item for item in record["effective"]["fields"] if item["key"] == field_key), None
    )
    field = next((item for item in raw.fields if item.key == field_key), None)
    decisions = record["decisions"]
    if action.action == "discard":
        if field_key not in decisions:
            raise Problem(
                404, "decision_not_found", "There is no saved review decision for this field."
            )
        decisions.pop(field_key)
    else:
        if field is None:
            raise Problem(404, "field_not_found", "Field not found.")
        value = None if action.action == "reject" else before["value"]
        source = None
        if action.action == "correct":
            value = normalize(field.kind, action.value)
            if value is None:
                raise Problem(422, "invalid_value", f"Enter a valid {field.kind} value.")
        if action.action == "approve" and value is None:
            raise Problem(422, "invalid_value", "Correct the missing value before approving it.")
        if action.box is not None:
            if not conn.execute(
                "SELECT 1 FROM pages WHERE org_id = %s AND document_id = %s AND number = %s",
                (org_id, document_id, action.page),
            ).fetchone():
                raise Problem(422, "page_not_found", "Select a page in the document.")
            source = Evidence(page=action.page, box=action.box, text="", score=1).model_dump(
                mode="json"
            )
        elif action.action != "reject":
            prior = decisions.get(field_key)
            if prior and prior["source"] == signature(field):
                source = prior["evidence"]
            if not field.evidence and source is None:
                raise Problem(422, "missing_evidence", "Draw a box around the source value.")
        decisions[field_key] = {
            "action": action.action,
            "value": value,
            "evidence": source,
            "source": signature(field),
        }
    result = validate(raw, decisions)
    updated = result.model_dump(mode="json")
    after = next((item for item in updated["fields"] if item["key"] == field_key), None)
    conn.execute(
        """UPDATE results SET effective = %s, decisions = %s WHERE org_id = %s AND document_id = %s""",
        (Jsonb(updated), Jsonb(decisions), org_id, document_id),
    )
    conn.execute(
        """INSERT INTO review_events (org_id, document_id, field_key, actor, event)
           VALUES (%s, %s, %s, %s, %s)""",
        (
            org_id,
            document_id,
            field_key,
            actor.key_id,
            Jsonb({"request": action.model_dump(mode="json"), "before": before, "after": after}),
        ),
    )
    conn.execute(
        """UPDATE documents SET status = %s, revision = revision + 1, updated_at = now()
           WHERE org_id = %s AND id = %s""",
        ("needs_review" if needs_review(result) else "completed", org_id, document_id),
    )
    return {"document": get_document(conn, org_id, document_id), **updated}
