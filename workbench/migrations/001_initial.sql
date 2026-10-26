CREATE TABLE organizations (
    id uuid PRIMARY KEY,
    name text NOT NULL
);

CREATE TABLE api_keys (
    digest bytea PRIMARY KEY CHECK (octet_length(digest) = 32),
    org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    label text NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE documents (
    org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    id uuid NOT NULL,
    filename text NOT NULL,
    sha256 bytea NOT NULL,
    schema_version text NOT NULL,
    pipeline_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('queued', 'processing', 'needs_review', 'completed', 'failed')),
    error_code text,
    generation integer NOT NULL DEFAULT 1,
    revision integer NOT NULL DEFAULT 1,
    page_count integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, id),
    UNIQUE (org_id, sha256, schema_version, pipeline_version)
);

CREATE INDEX documents_listing ON documents (org_id, created_at DESC, id DESC);
CREATE INDEX documents_review ON documents (org_id, created_at, id) WHERE status = 'needs_review';

CREATE TABLE uploads (
    org_id uuid NOT NULL,
    document_id uuid NOT NULL,
    source bytea NOT NULL,
    PRIMARY KEY (org_id, document_id),
    FOREIGN KEY (org_id, document_id) REFERENCES documents(org_id, id) ON DELETE CASCADE
);

CREATE TABLE idempotency_keys (
    org_id uuid NOT NULL,
    key text NOT NULL,
    document_id uuid NOT NULL,
    sha256 bytea NOT NULL,
    PRIMARY KEY (org_id, key),
    FOREIGN KEY (org_id, document_id) REFERENCES documents(org_id, id) ON DELETE CASCADE
);

CREATE TABLE pages (
    org_id uuid NOT NULL,
    document_id uuid NOT NULL,
    number integer NOT NULL CHECK (number > 0),
    image bytea NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (org_id, document_id, number),
    FOREIGN KEY (org_id, document_id) REFERENCES documents(org_id, id) ON DELETE CASCADE
);

CREATE TABLE jobs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id uuid NOT NULL,
    document_id uuid NOT NULL,
    generation integer NOT NULL,
    kind text NOT NULL CHECK (kind IN ('preflight', 'page', 'join')),
    page integer NOT NULL DEFAULT 0,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'running', 'done', 'failed')),
    attempts integer NOT NULL DEFAULT 0,
    lease uuid,
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz,
    UNIQUE (org_id, document_id, generation, kind, page),
    FOREIGN KEY (org_id, document_id) REFERENCES documents(org_id, id) ON DELETE CASCADE
);

CREATE INDEX jobs_pending ON jobs (available_at, id) WHERE state = 'pending';
CREATE INDEX jobs_leased ON jobs (lease_expires_at, id) WHERE state = 'running';

CREATE TABLE results (
    org_id uuid NOT NULL,
    document_id uuid NOT NULL,
    extracted jsonb NOT NULL,
    effective jsonb NOT NULL,
    decisions jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (org_id, document_id),
    FOREIGN KEY (org_id, document_id) REFERENCES documents(org_id, id) ON DELETE CASCADE
);

CREATE TABLE review_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id uuid NOT NULL,
    document_id uuid NOT NULL,
    field_key text NOT NULL,
    actor text NOT NULL,
    event jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (org_id, document_id) REFERENCES documents(org_id, id) ON DELETE CASCADE
);

CREATE INDEX review_events_document ON review_events (org_id, document_id, id);

CREATE TABLE worker_heartbeats (
    id uuid PRIMARY KEY,
    seen_at timestamptz NOT NULL
);
