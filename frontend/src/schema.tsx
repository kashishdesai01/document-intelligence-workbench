import { Braces, ShieldCheck } from "lucide-react";

import { PageBanner } from "./ui";

const statementFields = [
  ["account_id", "Alphanumeric or masked account identifier."],
  ["period_start / period_end", "ISO dates. The source may use U.S. month/day/year format."],
  ["opening_balance / closing_balance", "Exact decimal amounts represented as strings."],
];
const transactionFields = [
  ["date", "Posting date in ISO format."],
  ["description", "Transaction description as text."],
  ["amount", "Signed decimal amount. Separate debit and credit columns are combined."],
  ["balance", "Running balance after the transaction."],
];
const sampleData = {
  account_id: "***8109",
  period_start: "2025-03-01",
  period_end: "2025-03-31",
  opening_balance: "1050.00",
  closing_balance: "1027.00",
  transactions: [
    { date: "2025-03-02", description: "Subscription", amount: "-23.00", balance: "1027.00" },
  ],
};

function FieldList({ fields }: { fields: string[][] }) {
  return <dl className="schema-fields">{fields.map(([key, description]) => <div key={key}>
    <dt><code>{key}</code></dt><dd>{description}</dd>
  </div>)}</dl>;
}

export function Schema() {
  return <div className="content schema-page">
    <PageBanner title="Bank statement schema" description="bank-statement-usd@1 · USD · en-US"
      icon={<Braces size={30} />} />
    <div className="schema-grid">
      <section className="panel schema-panel" aria-label="Schema field reference">
        <h2>Statement fields</h2>
        <FieldList fields={statementFields} />
        <h2>Transaction fields</h2>
        <FieldList fields={transactionFields} />
      </section>
      <div className="schema-reference">
        <section className="panel schema-example" aria-label="JSON example">
          <h2>Example JSON data</h2>
          <pre><code>{JSON.stringify(sampleData, null, 2)}</code></pre>
        </section>
        <section className="panel schema-checks">
          <span className="upload-icon"><ShieldCheck size={19} /></span>
          <div><h2>Export checks</h2><p>Required values need page evidence. Running balances and the statement total
            must reconcile to the cent. Unmapped transactions block export.</p></div>
        </section>
      </div>
    </div>
  </div>;
}
