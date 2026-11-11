import { ArrowDown, ArrowRight, ArrowUpRight, Braces, ClipboardList, FileText, FlaskConical, Search, Upload } from "lucide-react";
import { useEffect, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import type { Client, Cursor, Document } from "./api";
import { active, Alert, displayDate, Loading, message, PageBanner, ScanPreview, Status } from "./ui";

type Listing = { documents: Document[]; next: Cursor | null };
const statuses = ["", "needs_review", "completed", "processing", "queued", "failed"];
const labels = ["All statuses", "Needs review", "Completed", "Processing", "Queued", "Failed"];
const workspaceLinks = [
  { to: "/review", icon: ClipboardList, title: "Review queue", description: "Resolve missing or mismatched values." },
  { to: "/schema", icon: Braces, title: "Statement schema", description: "See the fields available in exports." },
  { to: "/evaluation", icon: FlaskConical, title: "Extraction evaluation", description: "Explore results on synthetic statements." },
];

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
  const shortList = data !== null && data.documents.length <= 4;
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

  return <div className={queue ? "content queue-page" : "content documents-home"}>
    {queue ? <PageBanner title="Review queue" description="Resolve missing evidence and reconciliation issues."
      icon={<ClipboardList size={30} />} /> : <>
      <input ref={picker} className="visually-hidden" type="file" aria-label="Choose document"
        accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" onChange={choose} tabIndex={-1} />
      <header className={`upload-hero ${drag ? "drag" : ""}`}
        onDragOver={(event) => { event.preventDefault(); setDrag(true); }}
        onDragLeave={(event) => {
          if (!(event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget))) {
            setDrag(false);
          }
        }}
        onDrop={drop}>
        <div className="upload-hero-copy">
          <h1>Documents</h1>
          <p>Extract, verify, and export bank statement data.</p>
          <div className="upload-hero-actions">
            <button className="button primary" disabled={uploading} onClick={() => picker.current?.click()}>
              <Upload size={17} /> Upload document
            </button>
            <button className="button" onClick={sample} disabled={uploading}>
              <FlaskConical size={16} /> Try scanned sample
            </button>
          </div>
          <p className="upload-formats">Drop a PDF, PNG, JPEG, or TIFF here · Up to 50 MiB</p>
        </div>
        <ScanPreview />
      </header>
      {uploading && <Loading>Uploading document…</Loading>}
      {uploadError && <Alert>{uploadError} {pendingUpload.current && <button className="text-button"
        onClick={() => send(pendingUpload.current?.file)}>Retry upload</button>}</Alert>}
    </>}
    <div className={queue ? undefined : `documents-grid${shortList ? " short-list" : ""}`}>
      <section className="panel library-panel" aria-label={queue ? "Documents awaiting review" : "Uploaded documents"}>
        <div className="library-heading">
          <h2>{queue ? "Awaiting review" : "Document library"}</h2>
          {queue && data && <span>{data.documents.length} shown</span>}
        </div>
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
              <span className="file-icon"><FileText size={18} /></span><span title={item.filename}>{item.filename}</span>
            </Link></td>
            <td><Status value={item.status} /></td><td>{item.page_count ?? "—"}</td><td>{displayDate(item.created_at)}</td>
            <td><Link className="icon-button" to={`/documents/${item.id}`} aria-label={`Open ${item.filename}`}><ArrowRight size={17} /></Link></td>
          </tr>)}</tbody>
        </table></div> : data && <div className="empty">
          <span className="file-icon">{queue ? <ClipboardList size={23} /> : <FileText size={23} />}</span>
          <h2>{queue ? "No documents awaiting review" : search || status ? "No matching documents" : "No documents yet"}</h2>
          <p>{search || status ? "Try a different filename or status." : queue ? "Documents that need attention will appear here." : "Upload a statement or try the scanned sample to get started."}</p>
          {(search || status) && <button className="button small" onClick={() => { setSearch(""); setStatus(""); }}>Clear filters</button>}
        </div>}
        {((!queue && !!data?.documents.length) || data?.next) && <div className="panel-footer">
          {!queue && <span>{data?.documents.length} shown</span>}
          {data?.next && <button className="button" disabled={loadingMore} onClick={more}>
            <ArrowDown size={16} /> {loadingMore ? "Loading…" : "Load more"}
          </button>}
        </div>}
      </section>
      {!queue && <section className="panel workspace-links" aria-labelledby="workspace-links-title">
        <h2 id="workspace-links-title">Review &amp; reference</h2>
        {workspaceLinks.map(({ to, icon: Icon, title, description }) => <Link key={to} to={to}>
          <span className="workspace-link-icon"><Icon size={18} /></span>
          <span><strong>{title}</strong><small>{description}</small></span>
          <ArrowUpRight className="workspace-link-arrow" size={15} />
        </Link>)}
      </section>}
    </div>
  </div>;
}
