import { ArrowDown, ArrowRight, ClipboardList, FileText, FlaskConical, Search, Upload } from "lucide-react";
import { useEffect, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import type { Client, Cursor, Document } from "./api";
import { active, Alert, displayDate, Loading, message, Status } from "./ui";

type Listing = { documents: Document[]; next: Cursor | null };
const statuses = ["", "needs_review", "completed", "processing", "queued", "failed"];
const labels = ["All statuses", "Needs review", "Completed", "Processing", "Queued", "Failed"];

export function Documents({ api, queue }: { api: Client; queue: boolean }) {
  const [data, setData] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [uploading, setUploading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [drag, setDrag] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const pendingUpload = useRef<{ file: File; key: string } | null>(null);
  const navigate = useNavigate();
  const filter = queue ? "needs_review" : status;
  const order = queue ? "oldest" : "newest";
  const requestScope = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    requestScope.current = controller;
    let timer: number | undefined;
    setData(null);
    setError("");
    setLoadingMore(false);
    async function refresh() {
      try {
        const listing = await api.list(search.trim(), filter, undefined, controller.signal, order);
        if (controller.signal.aborted) return;
        setData((previous) => {
          if (!previous || previous.documents.length <= 40) return listing;
          const refreshed = new Set(listing.documents.map((item) => item.id));
          return {
            documents: [...listing.documents, ...previous.documents.slice(40).filter((item) => !refreshed.has(item.id))],
            next: previous.next,
          };
        });
        if (listing.documents.some((item) => active(item.status))) timer = window.setTimeout(refresh, 2500);
      } catch (error) {
        if (!controller.signal.aborted) setError(message(error));
      }
    }
    timer = window.setTimeout(refresh, search ? 250 : 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [api, search, filter, order]);

  async function more() {
    if (!data?.next || !requestScope.current) return;
    const { signal } = requestScope.current;
    setLoadingMore(true);
    try {
      const next = await api.list(search.trim(), filter, data.next, signal, order);
      if (!signal.aborted) setData((previous) => {
        const seen = new Set(previous?.documents.map((item) => item.id));
        return { documents: [...(previous?.documents ?? []), ...next.documents.filter((item) => !seen.has(item.id))], next: next.next };
      });
    } catch (error) {
      if (!signal.aborted) setError(message(error));
    } finally {
      if (!signal.aborted) setLoadingMore(false);
    }
  }

  async function send(file?: File) {
    if (!file || uploading) return;
    if (file.size > 50 * 1024 * 1024 || !file.size) {
      setUploadError("Choose a nonempty file up to 50 MiB.");
      return;
    }
    setUploading(true);
    setUploadError("");
    const key = pendingUpload.current?.file === file ? pendingUpload.current.key : crypto.randomUUID();
    pendingUpload.current = { file, key };
    try {
      const upload = await api.upload(file, key);
      pendingUpload.current = null;
      navigate(`/documents/${upload.document.id}`);
    } catch (error) {
      setUploadError(message(error));
    } finally {
      setUploading(false);
    }
  }

  async function sample() {
    if (uploading) return;
    setUploading(true);
    try {
      const response = await fetch("/samples/northstar-statement.pdf");
      if (!response.ok || !response.headers.get("content-type")?.includes("application/pdf")) {
        throw new Error("The sample statement could not be loaded.");
      }
      const file = new File([await response.blob()], "northstar-statement.pdf", { type: "application/pdf" });
      await send(file);
    } catch (error) {
      setUploadError(message(error));
    } finally {
      setUploading(false);
    }
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDrag(false);
    void send(event.dataTransfer.files[0]);
  }

  function choose(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    void send(file);
  }

  return <div className="content">
    <header className="page-heading">
      <div><h1>{queue ? "Review queue" : "Documents"}</h1>
        <p>{queue ? "Resolve missing evidence and reconciliation issues." : "Extract, verify, and export bank statement data."}</p></div>
      {!queue && <button className="button primary" disabled={uploading} onClick={() => picker.current?.click()}>
        <Upload size={17} /> Upload document
      </button>}
    </header>
    {!queue && <>
      <input ref={picker} className="visually-hidden" type="file" aria-label="Choose document"
        accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" onChange={choose} />
      <div className={`drop-zone ${drag ? "drag" : ""}`}
        onDragOver={(event) => { event.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)} onDrop={drop}>
        <span className="upload-icon"><Upload size={21} /></span>
        <div><strong>Drop a bank statement here</strong><p>PDF, PNG, JPEG, or TIFF · Up to 50 MiB</p></div>
        <button className="button" onClick={sample} disabled={uploading}><FlaskConical size={16} /> Try scanned sample</button>
      </div>
      {uploading && <Loading>Uploading document…</Loading>}
      {uploadError && <Alert>{uploadError} {pendingUpload.current && <button className="text-button"
        onClick={() => send(pendingUpload.current?.file)}>Retry upload</button>}</Alert>}
    </>}
    <section className="panel" aria-label={queue ? "Documents awaiting review" : "Uploaded documents"}>
      <div className="list-toolbar">
        <label className="search"><Search size={17} /><input value={search} onChange={(event) => setSearch(event.target.value)}
          placeholder="Search filenames" maxLength={120} aria-label="Search filenames" /></label>
        {!queue && <select aria-label="Filter by status" value={status} onChange={(event) => setStatus(event.target.value)}>
          {statuses.map((key, index) => <option key={key} value={key}>{labels[index]}</option>)}
        </select>}
      </div>
      {error && <Alert>{error}</Alert>}
      {!data && !error && <Loading />}
      {data?.documents.length ? <div className="table-scroll"><table className="document-table">
        <thead><tr><th>Document</th><th>Status</th><th>Pages</th><th>Uploaded</th><th><span className="visually-hidden">Open</span></th></tr></thead>
        <tbody>{data.documents.map((item) => <tr key={item.id}>
          <td><Link to={`/documents/${item.id}`} className="file-link">
            <span className="file-icon"><FileText size={18} /></span><span>{item.filename}</span>
          </Link></td>
          <td><Status value={item.status} /></td><td>{item.page_count ?? "—"}</td><td>{displayDate(item.created_at)}</td>
          <td><Link className="icon-button" to={`/documents/${item.id}`} aria-label={`Open ${item.filename}`}><ArrowRight size={17} /></Link></td>
        </tr>)}</tbody>
      </table></div> : data && <div className="empty">
        <span className="file-icon">{queue ? <ClipboardList size={23} /> : <FileText size={23} />}</span>
        <h2>{queue ? "No documents awaiting review" : "No documents found"}</h2>
        <p>{search || status ? "Try changing the current filters." : queue ? "Documents that need attention will appear here." : "Upload a statement or try the scanned sample to get started."}</p>
      </div>}
      {data?.next && <div className="panel-footer"><button className="button" disabled={loadingMore} onClick={more}>
        <ArrowDown size={16} /> {loadingMore ? "Loading…" : "Load more"}
      </button></div>}
    </section>
  </div>;
}
