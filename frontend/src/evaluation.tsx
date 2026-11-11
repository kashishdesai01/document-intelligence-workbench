import { Activity, ChartNoAxesCombined, Download, FileCheck, ShieldCheck, Table2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Alert, Loading, message, PageBanner } from "./ui";

type Metric = {
  documents: number;
  exact_documents: number;
  document_accuracy: number;
  straight_through: number;
  auto_accepted_error_rate: number;
  auto_accepted_fields: number;
  wrong_auto_accepted_fields: number;
  cell_f1: number;
  p95_seconds: number;
};

type Bin = { bin: number; count: number; mean_score: number; accuracy: number };
type Report = {
  dataset: string;
  pipeline: string;
  timestamp: string;
  variants: { full: Metric };
  calibration: {
    fit_split: string;
    fit_fields: number;
    raw: { ece: number; bins: Bin[] };
    isotonic: { ece: number; bins: Bin[] };
  };
};

const percent = (value: number) => `${(100 * value).toFixed(1)}%`;

export function Evaluation() {
  const [report, setReport] = useState<Report>();
  const [error, setError] = useState("");
  const [series, setSeries] = useState<"raw" | "isotonic">("raw");

  useEffect(() => {
    const controller = new AbortController();
    fetch("/evaluation/frozen.json", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("The frozen report could not be loaded.");
        return response.json();
      })
      .then((data: Report) => setReport(data))
      .catch((error) => { if (!controller.signal.aborted) setError(message(error)); });
    return () => controller.abort();
  }, []);

  const full = report?.variants.full;
  const calibration = report?.calibration[series];
  return <div className="content evaluation-page">
    <PageBanner title="Extraction evaluation" description="Frozen benchmark on generated native and scanned statements."
      icon={<ChartNoAxesCombined size={30} />} />
    {error && <Alert>{error}</Alert>}
    {!error && !report && <Loading />}
    {report && full && calibration && <>
      <section className="evaluation-cards" aria-label="Synthetic extraction metrics">
        <MetricCard icon={FileCheck} label="Document accuracy" value={percent(full.document_accuracy)}
          detail={`${full.exact_documents} of ${full.documents} documents exact`} />
        <MetricCard icon={Activity} label="Straight-through rate" value={percent(full.straight_through)}
          detail="Documents with zero review items" />
        <MetricCard icon={ShieldCheck} label="Auto-accepted error" value={percent(full.auto_accepted_error_rate)}
          detail={`${full.wrong_auto_accepted_fields} of ${full.auto_accepted_fields} accepted fields`} />
        <MetricCard icon={Table2} label="Transaction cell F1" value={percent(full.cell_f1)} detail="Exact values in statement order" />
      </section>
      <section className="panel reliability-panel">
        <header className="inspector-header"><div><h2>Reliability by evidence score</h2>
          <p>Expected calibration error {percent(calibration.ece)}</p></div>
          <div className="score-toggle" role="group" aria-label="Evidence score series">
            <button className="button small" aria-pressed={series === "raw"} onClick={() => setSeries("raw")}>Raw score</button>
            <button className="button small" aria-pressed={series === "isotonic"} onClick={() => setSeries("isotonic")}>Offline fit</button>
          </div>
        </header>
        <div className="reliability-layout">
          <div className="reliability-plot"><ReliabilityChart bins={calibration.bins} /></div>
          <div className="reliability-data">
            <table><caption>Held-out score bins</caption>
              <thead><tr><th>Mean score</th><th>Accuracy</th><th>Fields</th></tr></thead>
              <tbody>{calibration.bins.map((bin) => <tr key={bin.bin}>
                <td>{percent(bin.mean_score)}</td><td>{percent(bin.accuracy)}</td><td>{bin.count}</td>
              </tr>)}</tbody>
            </table>
            <p>Offline fit used {report.calibration.fit_fields} fields from {report.calibration.fit_split}. The review console shows raw, uncalibrated evidence scores.</p>
          </div>
        </div>
      </section>
      <footer className="panel evaluation-footer">
        <div><strong>{report.dataset}</strong>
          <p className="evaluation-meta">{report.pipeline} · p95 {full.p95_seconds.toFixed(2)} s · {new Date(report.timestamp).toLocaleDateString()}</p></div>
        <a className="button" href="/evaluation/frozen.json" download><Download size={16} /> Download benchmark JSON</a>
      </footer>
    </>}
  </div>;
}

function MetricCard({ icon: Icon, label, value, detail }: { icon: typeof FileCheck; label: string; value: string; detail: string }) {
  return <div className="panel metric-card">
    <span className="workspace-link-icon" aria-hidden="true"><Icon size={17} /></span>
    <h2>{label}</h2><strong>{value}</strong><p>{detail}</p>
  </div>;
}

function ReliabilityChart({ bins }: { bins: Bin[] }) {
  const x = (value: number) => 54 + value * 265;
  const y = (value: number) => 235 - value * 200;
  const ticks = [0, .25, .5, .75, 1];
  return <svg className="reliability-chart" viewBox="0 0 350 295" role="img"
    aria-label="Reliability diagram comparing mean evidence score with observed accuracy. Exact values are in the accompanying table.">
    <text x="55" y="19">Observed accuracy</text>
    {ticks.map((value) => <g key={value}>
      <line x1={x(0)} x2={x(1)} y1={y(value)} y2={y(value)} className="grid-line" />
      <text x="45" y={y(value) + 4} textAnchor="end">{value * 100}%</text>
      <text x={x(value)} y="255" textAnchor="middle">{value * 100}%</text>
    </g>)}
    <line x1={x(0)} x2={x(1)} y1={y(0)} y2={y(1)} className="ideal-line" />
    {bins.map((bin) => <circle key={bin.bin} cx={x(bin.mean_score)} cy={y(bin.accuracy)}
      r={Math.min(10, 4 + Math.sqrt(bin.count) / 6)} className="bin-point">
      <title>{percent(bin.mean_score)} score · {percent(bin.accuracy)} accuracy · {bin.count} fields</title>
    </circle>)}
    <text x="186" y="280" textAnchor="middle">Mean evidence score</text>
  </svg>;
}
