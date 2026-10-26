CREATE TABLE upload_windows (
    org_id uuid PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    started_at timestamptz NOT NULL,
    uploads integer NOT NULL CHECK (uploads > 0)
);
