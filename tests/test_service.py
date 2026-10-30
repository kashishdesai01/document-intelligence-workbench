import io
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from eval.synth import statement
from workbench.api import app
from workbench.db import connection_pool, migrate, provision
from workbench.worker import claim, finish, process_one, run_stage


@pytest.fixture
def system(monkeypatch):
    url = os.getenv("WORKBENCH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKBENCH_TEST_DATABASE_URL is required")
    monkeypatch.setenv("DATABASE_URL", url)
    migrate()
    org = uuid4()
    other = uuid4()
    key = uuid4().hex + uuid4().hex
    other_key = uuid4().hex + uuid4().hex
    provision(org, "Test", "reviewer", key)
    provision(other, "Other", "reviewer", other_key)
    with connection_pool() as pool, TestClient(app) as api:
        try:
            yield (
                api,
                pool,
                {"Authorization": f"Bearer {key}"},
                {"Authorization": f"Bearer {other_key}"},
            )
        finally:
            with pool.connection() as conn:
                conn.execute("DELETE FROM organizations WHERE id IN (%s, %s)", (org, other))


def send(api, headers, source, key="upload-1"):
    return api.post(
        "/api/v1/documents",
        files={"file": ("statement.pdf", source)},
        headers={
            **headers,
            "Idempotency-Key": key,
        },
    )


def drain(pool):
    for _ in range(20):
        if not process_one(pool):
            return
    pytest.fail("Queue did not drain")


@pytest.mark.parametrize("continuation", ["changed", "inherited", "same", "summary"])
def test_continuation_coverage_gates_both_exports(system, continuation):
    api, pool, auth, _ = system
    source = io.BytesIO()
    canvas = Canvas(source)

    def row(y, *values):
        for x, value in zip((52, 170, 360, 475), values, strict=True):
            canvas.drawString(x, y, value)

    canvas.drawString(52, 730, "Account Number: 123456")
    canvas.drawString(52, 700, "Statement Period: 2025-04-01 to 2025-04-30")
    canvas.drawString(52, 670, "Opening Balance: 100.00")
    row(620, "Date", "Description", "Amount", "Balance")
    row(590, "04/02/2025", "Deposit", "10.00", "110.00")
    if continuation in {"changed", "summary"}:
        canvas.drawString(52, 550, "Closing Balance: 110.00")
    canvas.showPage()
    if continuation == "summary":
        canvas.drawString(52, 660, "Interest paid through 04/30/2025: 0.00")
    elif continuation == "same":
        row(640, "04/03/2025", "Refund", "50.00", "160.00")
        row(610, "04/04/2025", "Reversal", "-50.00", "110.00")
    else:
        row(670, "Reference", "Posting date", "Change", "After posting")
        row(640, "Refund", "04/03/2025", "50.00", "160.00")
        row(610, "Reversal", "04/04/2025", "-50.00", "110.00")
    if continuation in {"inherited", "same"}:
        canvas.drawString(52, 550, "Closing Balance: 110.00")
    canvas.save()

    uploaded = send(api, auth, source.getvalue())
    assert uploaded.status_code == 202
    path = f"/api/v1/documents/{uploaded.json()['document']['id']}"
    drain(pool)

    result = api.get(f"{path}/result", headers=auth).json()
    unmapped = continuation in {"changed", "inherited"}
    assert result["document"]["status"] == ("needs_review" if unmapped else "completed")
    assert len(result["transactions"]) == (3 if continuation == "same" else 1)
    assert [issue["code"] for issue in result["issues"]] == (
        ["unmapped_transactions"] if unmapped else []
    )
    if unmapped:
        assert "page 2" in result["issues"][0]["message"]
    for format in ("json", "csv"):
        response = api.get(f"{path}/export", headers=auth, params={"format": format})
        assert response.status_code == (409 if unmapped else 200)
        if unmapped:
            assert response.json()["code"] == "review_required"


def test_workflow_tenant_boundaries_and_review_audit(system):
    api, pool, auth, other = system
    source, truth = statement(51, pages=2, broken=True)
    response = send(api, auth, source)
    assert response.status_code == 202, response.text
    doc_id = response.json()["document"]["id"]
    path = f"/api/v1/documents/{doc_id}"
    assert send(api, auth, source).json()["reused"]
    assert send(api, auth, statement(52)[0]).status_code == 409
    assert send(api, auth, source, key="upload-2").json()["document"]["id"] == doc_id
    assert send(api, other, source).json()["document"]["id"] != doc_id
    drain(pool)
    result = api.get(f"{path}/result", headers=auth).json()
    assert result["document"]["status"] == "needs_review"
    assert api.get(f"{path}/export", headers=auth).status_code == 409
    field_key = f"{result['transactions'][2]['key']}.amount"
    payload = {
        "action": "correct",
        "value": truth["transactions"][2]["amount"],
        "note": "Confirmed against the running balance.",
        "revision": result["document"]["revision"],
    }
    for suffix in ("", "/result", "/pages/1", "/pages/1/image", "/export", "/history"):
        assert api.get(path + suffix, headers=other).status_code == 404, suffix
    assert api.post(f"{path}/review/{field_key}", headers=other, json=payload).status_code == 404
    assert api.post(f"{path}/reprocess", headers=other).status_code == 404
    assert api.delete(path, headers=other).status_code == 404
    assert all(
        doc["id"] != doc_id
        for doc in api.get("/api/v1/documents", headers=other).json()["documents"]
    )
    response = api.post(f"{path}/review/{field_key}", headers=auth, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["document"]["status"] == "completed"
    assert (
        api.post(f"{path}/review/{field_key}", headers=auth, json=payload).json()["code"]
        == "stale_revision"
    )
    exported = api.get(f"{path}/export", headers=auth)
    assert exported.status_code == 200
    assert exported.json()["data"]["transactions"][2]["amount"] == "-170.27"
    correction = next(item for item in exported.json()["fields"] if item["key"] == field_key)
    assert correction["status"] == "corrected"
    assert correction["value"] == "-170.27"
    events = api.get(f"{path}/history", headers=auth).json()["events"]
    assert len(events) == 1 and events[0]["event"]["before"]["value"] == "-160.27"
    assert api.post(f"{path}/reprocess", headers=auth).status_code == 202
    drain(pool)
    assert api.get(path, headers=auth).json()["status"] == "completed"
    assert api.post(f"{path}/reprocess", headers=auth).status_code == 202
    for _ in range(3):
        assert process_one(pool)  # Preflight and two pages.
    join = claim(pool)
    assert join.kind == "join"
    extracted = run_stage(pool, join)
    changed = next(field for field in extracted.fields if field.key == field_key)
    changed.evidence[0].score = 0.95
    finish(pool, join, extracted)
    updated = api.get(f"{path}/result", headers=auth).json()
    assert updated["document"]["status"] == "needs_review"
    assert any(issue["conflict_key"] == field_key for issue in updated["issues"])
    assert api.get(f"{path}/export", headers=auth).status_code == 409
    payload["revision"] = updated["document"]["revision"]
    assert (
        api.post(f"{path}/review/{field_key}", headers=auth, json=payload).json()["document"][
            "status"
        ]
        == "completed"
    )
    assert api.get(f"{path}/pages/1/image", headers=auth).headers["cache-control"] == "no-store"
    assert api.delete(path, headers=auth).status_code == 204
    for suffix in ("", "/result", "/pages/1", "/pages/1/image", "/export", "/history"):
        assert api.get(path + suffix, headers=auth).status_code == 404


def test_concurrent_admission_and_stale_lease(system):
    api, pool, auth, _ = system
    source = statement(51)[0]
    with ThreadPoolExecutor(max_workers=3) as executor:
        responses = list(executor.map(lambda _: send(api, auth, source), range(3)))
    assert all(response.status_code == 202 for response in responses)
    assert len({response.json()["document"]["id"] for response in responses}) == 1
    assert sum(not response.json()["reused"] for response in responses) == 1
    first = claim(pool)
    assert first.kind == "preflight"
    output = run_stage(pool, first)
    with pool.connection() as conn:
        conn.execute("UPDATE jobs SET lease_expires_at = now() WHERE id = %s", (first.id,))
    second = claim(pool)
    assert second.id == first.id and second.lease != first.lease
    finish(pool, first, output)
    path = f"/api/v1/documents/{first.document_id}"
    assert api.get(path, headers=auth).json()["status"] == "queued"
    finish(pool, second, output)
    drain(pool)
    assert api.get(path, headers=auth).json()["status"] == "completed"


def test_invalid_upload_and_access(system):
    api, pool, auth, _ = system
    assert api.get("/api/v1/documents").status_code == 401
    assert send(api, auth, b"").status_code == 422
    response = send(api, auth, b"<html>bad type</html>")
    doc_id = response.json()["document"]["id"]
    drain(pool)
    result = api.get(f"/api/v1/documents/{doc_id}", headers=auth).json()
    assert result["status"] == "failed"
    assert result["error_code"] == "unsupported_type"


def test_chunked_upload_size_and_review_body_are_bounded(system, monkeypatch):
    api, pool, auth, _ = system
    monkeypatch.setattr("workbench.api.MAX_BYTES", 4096)
    response = send(api, auth, b"x" * 70000)
    assert response.status_code == 413, response.text
    assert response.json()["code"] == "file_too_large"
    doc = send(api, auth, statement(7)[0]).json()["document"]
    drain(pool)
    response = api.post(
        f"/api/v1/documents/{doc['id']}/review/account_id",
        headers=auth,
        json={
            "action": "approve",
            "note": "x" * 9000,
            "revision": 2,
        },
    )
    assert response.status_code == 413, response.text
    assert response.json()["code"] == "request_too_large"
    assert api.get(f"/api/v1/documents/{doc['id']}/history", headers=auth).json()["events"] == []


def test_deleted_uploads_still_consume_rate_limit(system):
    api, _, auth, _ = system
    for index in range(15):
        response = send(api, auth, f"document {index}".encode(), key=f"upload-{index}")
        assert response.status_code == 202
        doc = response.json()["document"]
        assert api.delete(f"/api/v1/documents/{doc['id']}", headers=auth).status_code == 204
    assert send(api, auth, b"new statement", key="limit-test").status_code == 429


def test_concurrent_page_completion_enqueues_one_join(system):
    api, pool, auth, _ = system
    source, _ = statement(18, pages=2)
    doc = send(api, auth, source).json()["document"]
    assert process_one(pool)  # Preflight fans out two independent page jobs.
    jobs = [claim(pool), claim(pool)]
    assert {job.page for job in jobs} == {1, 2}

    def complete(job):
        finish(pool, job, run_stage(pool, job))

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(complete, jobs))
    with pool.connection() as conn:
        count = conn.execute(
            "SELECT count(*) AS n FROM jobs WHERE document_id = %s AND kind = 'join'", (doc["id"],)
        ).fetchone()["n"]
    assert count == 1
    assert process_one(pool)
    assert api.get(f"/api/v1/documents/{doc['id']}", headers=auth).json()["status"] == "completed"


def test_null_characters_in_review_input_return_validation_error(system):
    api, pool, auth, _ = system
    response = send(api, auth, statement(91)[0])
    doc_id = response.json()["document"]["id"]
    drain(pool)
    path = f"/api/v1/documents/{doc_id}"
    revision = api.get(path, headers=auth).json()["revision"]
    response = api.post(
        f"{path}/review/account_id",
        headers=auth,
        json={
            "action": "approve",
            "note": "invalid\x00note",
            "revision": revision,
        },
    )
    assert response.status_code == 422
    assert api.get("/api/v1/documents?search=%00", headers=auth).status_code == 422
