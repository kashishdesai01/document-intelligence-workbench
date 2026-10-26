import argparse
import hashlib
import os
import secrets
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SCHEMA_VERSION = "bank-statement-usd@1"
PIPELINE_VERSION = "statement-layout@1"


def database_url() -> str:
    return os.environ["DATABASE_URL"]


def connection_pool() -> ConnectionPool:
    return ConnectionPool(
        database_url(),
        min_size=1,
        max_size=8,
        timeout=5,
        kwargs={"row_factory": dict_row},
        open=False,
    )


def migrate() -> None:
    with psycopg.connect(database_url()) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(68027311)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS migrations (name text PRIMARY KEY, sha256 text NOT NULL)"
        )
        for path in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
            source = path.read_bytes()
            digest = hashlib.sha256(source).hexdigest()
            previous = conn.execute(
                "SELECT sha256 FROM migrations WHERE name = %s", (path.name,)
            ).fetchone()
            if previous is not None:
                if previous[0] != digest:
                    raise RuntimeError(f"Migration checksum changed for {path.name}")
                continue
            conn.execute(source.decode())
            conn.execute("INSERT INTO migrations VALUES (%s, %s)", (path.name, digest))


def digest_key(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


def provision(org_id: UUID, name: str, label: str, key: str) -> None:
    with psycopg.connect(database_url()) as conn:
        conn.execute(
            "INSERT INTO organizations VALUES (%s, %s) ON CONFLICT DO NOTHING", (org_id, name)
        )
        conn.execute(
            "INSERT INTO api_keys (digest, org_id, label) VALUES (%s, %s, %s)",
            (
                digest_key(key),
                org_id,
                label,
            ),
        )


def main() -> None:
    cli = argparse.ArgumentParser()
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    create = commands.add_parser("create-key")
    create.add_argument("--org", type=UUID, default=None)
    create.add_argument("--name", default="Workbench")
    create.add_argument("--label", default="operator")
    args = cli.parse_args()
    if args.command == "migrate":
        migrate()
        return
    key = "diw_" + secrets.token_urlsafe(32)
    org_id = args.org or uuid4()
    provision(org_id, args.name, args.label, key)
    print(f"Organization {org_id}\nBearer key {key}")


if __name__ == "__main__":
    main()
