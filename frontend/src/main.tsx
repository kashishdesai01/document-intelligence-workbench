import { Braces, ClipboardList, FileStack, FlaskConical, KeyRound, LogOut, ScanLine, ShieldCheck } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, NavLink, Route, Routes, useParams } from "react-router-dom";

import { client, type Client } from "./api";
import { Detail } from "./detail";
import { Documents } from "./documents";
import { Evaluation } from "./evaluation";
import { Alert, message } from "./ui";
import "./styles.css";

const SESSION_KEY = "workbench.workspace-key";

function Brand() {
  return <><span className="brand-icon"><ScanLine size={22} /></span><span>Document Intelligence<small>Workbench</small></span></>;
}

function Connect({ onConnect }: { onConnect: (key: string) => void }) {
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await client(key.trim()).connect();
      onConnect(key.trim());
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }

  return <main className="connect">
    <div className="connect-inner">
      <div className="brand"><Brand /></div>
      <section className="panel connect-card">
        <span className="upload-icon"><KeyRound size={20} /></span>
        <h1>Connect to your workspace</h1>
        <p>Enter your workspace API key to upload and review bank statements.</p>
        <form onSubmit={submit}>
          <label>Workspace API key<input type="password" value={key} onChange={(event) => setKey(event.target.value)}
            autoComplete="off" spellCheck={false} maxLength={128} required /></label>
          {error && <Alert>{error}</Alert>}
          <button className="button primary" type="submit" disabled={!key.trim() || busy}>{busy ? "Connecting…" : "Connect"}</button>
        </form>
      </section>
    </div>
  </main>;
}

function DetailRoute({ api }: { api: Client }) {
  const { id = "" } = useParams();
  return <Detail key={id} api={api} id={id} />;
}

function Workspace({ api, disconnect }: { api: Client; disconnect: () => void }) {
  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to content</a>
    <aside className="sidebar">
      <Link to="/" className="brand"><Brand /></Link>
      <nav aria-label="Workspace">
        <NavLink end to="/"><FileStack size={18} /> Documents</NavLink>
        <NavLink to="/review"><ClipboardList size={18} /> Review queue</NavLink>
        <NavLink to="/evaluation"><FlaskConical size={18} /> Evaluation</NavLink>
        <NavLink to="/schema"><Braces size={18} /> Schema</NavLink>
      </nav>
      <button className="disconnect" onClick={disconnect}><LogOut size={17} /> Disconnect</button>
    </aside>
    <div className="main-shell">
      <div className="topbar"><span>Workspace</span><span className="workspace-state"><ShieldCheck size={15} /> Key authenticated</span></div>
      <main id="main-content">
        <Routes>
          <Route path="/" element={<Documents key="documents" api={api} queue={false} />} />
          <Route path="/review" element={<Documents key="review" api={api} queue />} />
          <Route path="/documents/:id" element={<DetailRoute api={api} />} />
          <Route path="/evaluation" element={<Evaluation />} />
          <Route path="/schema" element={<div className="content">
            <header className="page-heading"><div><h1>Bank statement schema</h1><p>bank-statement-usd@1 · USD · en-US</p></div></header>
            <section className="panel schema-panel"><h2>Statement fields</h2>
              <dl><dt>Account ID</dt><dd>Alphanumeric or masked account identifier.</dd>
                <dt>Period start and end</dt><dd>ISO dates. Source dates may use U.S. month/day/year format.</dd>
                <dt>Opening and closing balances</dt><dd>Exact decimal amounts represented as strings.</dd></dl>
              <h2>Transactions</h2><p>Each row requires a date, description, signed amount, and running balance.
                Separate debit and credit columns are converted to a signed amount with both source regions retained.</p>
              <h2>Export checks</h2><p>Required values need page evidence. Running balances and the statement total
                must reconcile to the cent before export.</p>
            </section>
          </div>} />
          <Route path="*" element={<div className="content"><h1>Page not found</h1><Link to="/">Back to documents</Link></div>} />
        </Routes>
      </main>
    </div>
  </div>;
}

function App() {
  const [key, setKey] = useState(() => sessionStorage.getItem(SESSION_KEY) ?? "");
  const api = useMemo(() => client(key), [key]);

  function connect(value: string) {
    if (value) sessionStorage.setItem(SESSION_KEY, value);
    else sessionStorage.removeItem(SESSION_KEY);
    setKey(value);
  }

  return key ? <Workspace api={api} disconnect={() => connect("")} /> : <Connect onConnect={connect} />;
}

createRoot(document.getElementById("root")!).render(<BrowserRouter><App /></BrowserRouter>);
