import {
  ArrowLeft, ChevronLeft, ChevronRight, CircleAlert, Download, FileText, History, Pencil,
  RefreshCw, ScanLine, Trash2,
} from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import type { AuditEvent, Box, Client, Document, Field, Result, ReviewRequest } from "./api";
import { active, Alert, displayDate, label, Loading, message, Status } from "./ui";
import { Viewer } from "./viewer";

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function Detail({ api, id }: { api: Client; id: string }) {
  const [doc, setDoc] = useState<Document>();
  const [result, setResult] = useState<Result>();
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [box, setBox] = useState<Box | null>(null);
  const [edit, setEdit] = useState("");
  const [note, setNote] = useState("");
  const [tab, setTab] = useState<"fields" | "transactions" | "history">("fields");
  const [events, setEvents] = useState<AuditEvent[]>();
  const [nextEvent, setNextEvent] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [reload, setReload] = useState(0);
  const navigate = useNavigate();
  const inspector = useRef<HTMLElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    let delay = 400;
    async function refresh() {
      try {
        const document = await api.document(id, controller.signal);
        if (controller.signal.aborted) return;
        setDoc(document);
        if (active(document.status)) {
          timer = window.setTimeout(refresh, delay);
          delay = Math.min(2400, delay * 1.5);
        } else if (document.status !== "failed") {
          const data = await api.result(id, controller.signal);
          if (!controller.signal.aborted) {
            setDoc(data.document);
            setResult(data);
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) setError(message(error));
      }
    }
    void refresh();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [api, id, reload]);

  useEffect(() => {
    if (tab !== "history") return;
    const controller = new AbortController();
    setEvents(undefined);
    api.history(id, undefined, controller.signal).then((data) => {
      if (!controller.signal.aborted) { setEvents(data.events); setNextEvent(data.next); }
    }).catch((error) => { if (!controller.signal.aborted) setError(message(error)); });
    return () => controller.abort();
  }, [api, id, tab, doc?.revision]);

  const fields = result?.fields ?? [];
  const field = fields.find((item) => item.key === selected);
  const pending = fields.filter((item) => item.status === "needs_review" || item.status === "rejected");
  const byKey = new Map(fields.map((item) => [item.key, item]));
  const conflict = result?.issues.some((issue) =>
    issue.code === "review_conflict" && issue.conflict_key === selected
  );

  function choose(key: string, switchTab = true) {
    if (busy) return;
    const next = byKey.get(key);
    setSelected(key);
    setEdit(next?.value ?? "");
    setNote("");
    setBox(null);
    setDrawing(false);
    if (next?.evidence[0]) setPage(next.evidence[0].page);
    if (switchTab) setTab(key.includes(".") ? "transactions" : "fields");
  }

  function go(number: number) {
    if (busy) return;
    setPage(number);
    setBox(null);
    setDrawing(false);
  }

  function nextPending(direction: 1 | -1) {
    const index = pending.findIndex((item) => item.key === selected);
    if (!pending.length) return;
    const position = index < 0 ? (direction > 0 ? 0 : pending.length - 1) : (index + direction + pending.length) % pending.length;
    choose(pending[position].key);
  }

  async function perform(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }

  async function submit(action: ReviewRequest["action"], key = selected) {
    if (!key || !doc || !note.trim()) return;
    await perform(async () => {
      const request: ReviewRequest = { action, revision: doc.revision, note };
      if (action === "correct") request.value = edit;
      if (box && (action === "correct" || action === "approve")) {
        request.box = box;
        request.page = page;
      }
      const next = await api.review(id, key, request);
      setResult(next);
      setDoc(next.document);
      setEdit(next.fields.find((item) => item.key === key)?.value ?? "");
      setNote("");
      setBox(null);
      setDrawing(false);
    });
  }

  async function moreEvents() {
    await perform(async () => {
      if (!nextEvent) return;
      const next = await api.history(id, nextEvent);
      setEvents((previous) => [...(previous ?? []), ...next.events]);
      setNextEvent(next.next);
    });
  }

  function reprocess() {
    void perform(async () => {
      setDoc(await api.reprocess(id));
      setResult(undefined);
      setSelected(null);
      setPage(1);
      setBox(null);
      setDrawing(false);
      setReload((count) => count + 1);
    });
  }

  function inspectIssue(key: string) {
    choose(key);
    inspector.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    inspector.current?.focus({ preventScroll: true });
  }

  function correct(event: FormEvent) {
    event.preventDefault();
    void submit("correct");
  }

  return <div className="content detail-content">
    <Link to="/" className="back"><ArrowLeft size={16} /> Documents</Link>
    <header className="page-heading detail-heading">
      <div><div className="title-line"><h1>{doc?.filename ?? "Document"}</h1>{doc && <Status value={doc.status} />}</div>
        {doc && <p>Bank statement <span className="mid-dot">·</span> {doc.page_count ?? "—"} {doc.page_count === 1 ? "page" : "pages"} <span className="mid-dot">·</span> Uploaded {displayDate(doc.created_at)}</p>}
      </div>
      {doc && <div className="actions">
        <button className="icon-button bordered" onClick={reprocess} disabled={busy || active(doc.status)} aria-label="Reprocess document" title="Reprocess document"><RefreshCw size={17} /></button>
        <button className="icon-button bordered" onClick={() => setConfirmDelete(!confirmDelete)} disabled={busy} aria-label="Delete document" title="Delete document"><Trash2 size={17} /></button>
        <button className="button" disabled={busy || doc.status !== "completed"} onClick={() => perform(async () => download(await api.export(id, "csv"), "transactions.csv"))}>CSV</button>
        <button className="button primary" disabled={busy || doc.status !== "completed"} onClick={() => perform(async () => download(await api.export(id, "json"), "statement.json"))}><Download size={17} /> Export JSON</button>
      </div>}
    </header>
    {confirmDelete && <div className="notice"><div><strong>Delete this document?</strong><p>The source, extracted values, and review history will be removed from this workspace.</p></div>
      <button className="button" onClick={() => setConfirmDelete(false)}>Cancel</button>
      <button className="button danger" disabled={busy} onClick={() => perform(async () => { await api.remove(id); navigate("/"); })}>Delete</button></div>}
    {error && <Alert>{error} <button className="text-button" onClick={() => { setError(""); setReload((count) => count + 1); }}>Reload</button></Alert>}
    {!doc && !error && <Loading />}
    {doc && active(doc.status) && <section className="panel processing-state"><Loading>{doc.status === "queued" ? "Waiting for a worker…" : "Reading pages and reconciling transactions…"}</Loading></section>}
    {doc?.status === "failed" && <Alert>Processing failed ({doc.error_code?.replaceAll("_", " ")}). You can reprocess the document or upload a new copy.</Alert>}
    {doc && result && !active(doc.status) && doc.status !== "failed" && <>
      {result.issues.length > 0 && <section className="reconciliation" aria-label="Reconciliation issues">
        <CircleAlert size={19} />
        <div><strong>{result.issues.length} {result.issues.length === 1 ? "issue" : "issues"} to resolve</strong>
          <ul>{result.issues.map((issue, index) => <li key={`${issue.code}-${index}`}>
            {issue.message} {issue.field_keys[0] && <button className="text-button" onClick={() => inspectIssue(issue.field_keys[0])}>Inspect</button>}
            {issue.code === "review_conflict" && issue.conflict_key && <button className="text-button"
              onClick={() => { choose(issue.conflict_key as string); setTab("fields"); }}>Discard previous decision</button>}
          </li>)}</ul></div>
      </section>}
      <div className="review-layout">
        <Viewer api={api} id={id} generation={doc.generation} page={page} count={doc.page_count ?? 1}
          fields={fields} selected={selected} drawing={drawing} box={box} go={go}
          select={(key) => choose(key)} draw={(next) => { setBox(next); setDrawing(false); }} />
        <section className="panel inspector" aria-label="Extraction review" ref={inspector} tabIndex={-1}>
          <header className="inspector-header">
            <div><h2>Extraction</h2><p>{pending.length ? `${pending.length} of ${fields.length} fields need review` : `${fields.length} fields verified`}</p></div>
            <div className="pagination">
              <button className="icon-button" aria-label="Previous review item" disabled={!pending.length} onClick={() => nextPending(-1)}><ChevronLeft size={17} /></button>
              <button className="icon-button" aria-label="Next review item" disabled={!pending.length} onClick={() => nextPending(1)}><ChevronRight size={17} /></button>
            </div>
          </header>
          <div className="tabs" aria-label="Extraction views">
            {(["fields", "transactions", "history"] as const).map((value) => <button key={value} type="button"
              aria-pressed={tab === value} onClick={() => setTab(value)}>{value === "history" && <History size={14} />}
              {label(value)} {value === "transactions" && <span>{result.transactions.length}</span>}
            </button>)}
          </div>
          {tab === "fields" && <div className="field-list">{fields.filter((item) => !item.key.includes(".")).map((item) =>
            <FieldButton key={item.key} field={item} selected={selected === item.key} choose={() => choose(item.key)} />)}</div>}
          {tab === "transactions" && <div className="transaction-list table-scroll">
            <table><thead><tr><th>Date</th><th>Description</th><th>Amount</th><th>Balance</th></tr></thead>
              <tbody>{result.transactions.map((row) => <tr key={row.key}>{["date", "description", "amount", "balance"].map((name) => {
                const key = `${row.key}.${name}`;
                const item = byKey.get(key);
                return <td key={name}><button className={`cell ${item?.status ?? ""}`}
                  aria-label={`${label(name)} ${item?.value ?? "missing"} on page ${row.page}`}
                  aria-pressed={selected === key} onClick={() => choose(key)}>{item?.value ?? "—"}</button></td>;
              })}</tr>)}</tbody></table>
          </div>}
          {tab === "history" && <div className="history">
            {!events && <Loading />}
            {events?.length === 0 && <p>No review actions recorded.</p>}
            {events?.map((item) => <div className="history-item" key={item.id}>
              <strong>{label(item.event.request.action)} · {label(item.field_key)}</strong>
              <time>{displayDate(item.created_at)}</time>
              <p>{item.event.request.note}</p>
              {item.event.before?.value !== item.event.after?.value && <p>{item.event.before?.value ?? "Missing"} → {item.event.after?.value ?? "Missing"}</p>}
            </div>)}
            {nextEvent && <button className="button" disabled={busy} onClick={moreEvents}>Load more</button>}
          </div>}
          {selected && tab !== "history" && <form className="edit-panel" onSubmit={correct}>
            <div className="edit-heading"><Pencil size={16} /><h3>{field ? label(field.key) : "Previous review decision"}</h3>{field && <Status value={field.status} />}</div>
            {field && <>
              {field.evidence[0]?.text && <p className="evidence-text">Source reads <strong>{field.evidence[0].text}</strong></p>}
              {field.score !== null && <p className="evidence-text">Evidence score {Math.round(field.score * 100)}% · Uncalibrated</p>}
              <label>Value<input value={edit} onChange={(event) => setEdit(event.target.value)} maxLength={300} /></label>
              <button className="button small" type="button" onClick={() => setDrawing(!drawing)}><ScanLine size={15} />
                {drawing ? "Cancel drawing" : box ? "Redraw evidence box" : "Draw evidence box"}</button>
              {drawing && <p className="evidence-text">Drag over the source value on page {page}.</p>}
              {box && <p className="evidence-text">New evidence region selected on page {page}.</p>}
            </>}
            <label>Review note<textarea value={note} onChange={(event) => setNote(event.target.value)} required maxLength={500} rows={2} /></label>
            <div className="actions">
              {field && <>
                <button type="submit" className="button primary small" disabled={busy || !note.trim() || !edit.trim()}>Save correction</button>
                <button type="button" className="button small" disabled={busy || !note.trim() || !field.value}
                  onClick={() => submit("approve")}>Approve</button>
                <button type="button" className="button small" disabled={busy || !note.trim()} onClick={() => submit("reject")}>Reject</button>
              </>}
              {conflict &&
                <button type="button" className="button small" disabled={busy || !note.trim()} onClick={() => submit("discard")}>Discard saved decision</button>}
            </div>
          </form>}
          {!selected && tab !== "history" && <div className="inspector-hint"><FileText size={17} /> Select a field or evidence box to review it.</div>}
        </section>
      </div>
    </>}
  </div>;
}

function FieldButton({ field, selected, choose }: { field: Field; selected: boolean; choose: () => void }) {
  return <button className="field-row" onClick={choose} aria-pressed={selected}>
    <span><span className="field-label">{label(field.key)}</span><strong>{field.value ?? "Missing"}</strong></span>
    <Status value={field.status} />
  </button>;
}
