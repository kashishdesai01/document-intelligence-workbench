export type DocumentStatus = "queued" | "processing" | "needs_review" | "completed" | "failed";
export type FieldStatus = "auto_accepted" | "needs_review" | "corrected" | "approved" | "rejected";
export type Box = { left: number; top: number; right: number; bottom: number };
export type Document = {
  id: string;
  filename: string;
  status: DocumentStatus;
  page_count: number | null;
  revision: number;
  generation: number;
  schema_version: string;
  pipeline_version: string;
  created_at: string;
  updated_at: string;
  error_code: string | null;
};
export type Evidence = { page: number; box: Box; text: string; score: number };
export type Field = {
  key: string;
  kind: "money" | "date" | "id" | "text";
  value: string | null;
  evidence: Evidence[];
  status: FieldStatus;
  score: number | null;
  issues: string[];
};
export type Issue = { code: string; message: string; field_keys: string[]; conflict_key: string | null };
export type Result = {
  document: Document;
  fields: Field[];
  transactions: { key: string; page: number }[];
  issues: Issue[];
};
export type Cursor = { before_time: string; before_id: string };
export type Page = { number: number; width: number; height: number; source: "native" | "ocr"; rotation: number };
export type AuditEvent = {
  id: number;
  field_key: string;
  created_at: string;
  event: {
    request: { action: string; note: string };
    before: Field | null;
    after: Field | null;
  };
};
export type ReviewRequest = {
  revision: number;
  action: "approve" | "correct" | "reject" | "discard";
  value?: string;
  page?: number;
  box?: Box;
  note: string;
};

export class ApiError extends Error {
  constructor(public status: number, public code: string, detail: string) {
    super(detail);
  }
}

export function client(key: string) {
  async function request(path: string, init?: RequestInit): Promise<Response> {
    const headers = new Headers(init?.headers);
    headers.set("Authorization", `Bearer ${key}`);
    const response = await fetch(`/api/v1${path}`, { ...init, headers, cache: "no-store" });
    if (!response.ok) {
      const problem = await response.json().catch(() => ({}));
      throw new ApiError(response.status, problem.code ?? "", problem.detail ?? `Request failed (${response.status}).`);
    }
    return response;
  }

  async function json<T>(path: string, init?: RequestInit): Promise<T> {
    return (await request(path, init)).json();
  }

  const path = (id: string) => `/documents/${encodeURIComponent(id)}`;
  return {
    connect: () => json<unknown>("/schemas"),
    list: (search: string, status: string, cursor?: Cursor, signal?: AbortSignal, order = "newest") => {
      const parameters = new URLSearchParams({ order });
      if (search) parameters.set("search", search);
      if (status) parameters.set("status", status);
      if (cursor) {
        parameters.set("before_time", cursor.before_time);
        parameters.set("before_id", cursor.before_id);
      }
      return json<{ documents: Document[]; next: Cursor | null }>(`/documents?${parameters}`, { signal });
    },
    upload: (file: File, idempotencyKey: string) => {
      const body = new FormData();
      body.append("file", file);
      return json<{ document: Document; reused: boolean }>("/documents", {
        method: "POST", body, headers: { "Idempotency-Key": idempotencyKey },
      });
    },
    document: (id: string, signal?: AbortSignal) => json<Document>(path(id), { signal }),
    result: (id: string, signal?: AbortSignal) => json<Result>(`${path(id)}/result`, { signal }),
    page: async (id: string, number: number, signal?: AbortSignal) => {
      const [metadata, image] = await Promise.all([
        json<Page>(`${path(id)}/pages/${number}`, { signal }),
        request(`${path(id)}/pages/${number}/image`, { signal }).then((response) => response.blob()),
      ]);
      return { metadata, image };
    },
    review: (id: string, field: string, body: ReviewRequest) =>
      json<Result>(`${path(id)}/review/${encodeURIComponent(field)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      }),
    history: (id: string, before?: number, signal?: AbortSignal) =>
      json<{ events: AuditEvent[]; next: number | null }>(
        `${path(id)}/history${before ? `?before=${before}` : ""}`, { signal },
      ),
    reprocess: (id: string) => json<Document>(`${path(id)}/reprocess`, { method: "POST" }),
    remove: (id: string) => request(path(id), { method: "DELETE" }),
    export: async (id: string, format: "json" | "csv") =>
      (await request(`${path(id)}/export?format=${format}`)).blob(),
  };
}

export type Client = ReturnType<typeof client>;
