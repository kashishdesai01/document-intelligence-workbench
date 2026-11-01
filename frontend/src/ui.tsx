import { CircleAlert, LoaderCircle } from "lucide-react";
import type { ReactNode } from "react";

import type { DocumentStatus, FieldStatus } from "./api";

const statusLabels: Record<DocumentStatus | FieldStatus, string> = {
  queued: "Queued", processing: "Processing", needs_review: "Needs review",
  completed: "Completed", failed: "Failed", auto_accepted: "Accepted",
  corrected: "Corrected", approved: "Approved", rejected: "Rejected",
};

export function Status({ value }: { value: DocumentStatus | FieldStatus }) {
  return <span className={`status ${value}`}><span className="status-dot" />{statusLabels[value]}</span>;
}

export function Alert({ children }: { children: ReactNode }) {
  return <p className="alert" role="alert"><CircleAlert size={17} /> <span>{children}</span></p>;
}

export function Loading({ children = "Loading…" }: { children?: ReactNode }) {
  return <div className="loading" role="status"><LoaderCircle className="spin" size={18} />{children}</div>;
}

export function displayDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export function label(key: string) {
  const raw = key.split(".").at(-1) ?? key;
  if (raw === "account_id") return "Account ID";
  return raw.charAt(0).toUpperCase() + raw.slice(1).replaceAll("_", " ");
}

export function message(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong.";
}

export const active = (status: DocumentStatus) => status === "queued" || status === "processing";
