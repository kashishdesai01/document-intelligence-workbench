# Development and operations

## Run directly for development

Use Python 3.12, uv, Node.js 22, PostgreSQL, and Tesseract with English and orientation data. The services use loopback ports 8010 (API), 8011 (parser), and 5173 (Vite). Provide a dedicated development database and a separate test database.

```sh
uv sync --frozen
npm ci --prefix frontend
export DATABASE_URL='postgresql://workbench:password@127.0.0.1:5432/workbench'
uv run python -m workbench.db migrate
uv run python -m workbench.db create-key
```

Run these in separate terminals, each with the relevant environment:

```sh
uv run uvicorn workbench.api:app --host 127.0.0.1 --port 8010 --no-access-log
uv run uvicorn workbench.parser_api:app --host 127.0.0.1 --port 8011 --no-access-log
PARSER_URL=http://127.0.0.1:8011 uv run python -m workbench.worker
npm run dev --prefix frontend
```

The worker can also call a subprocess directly when `PARSER_URL` is unset. Use the isolated parser service for untrusted inputs.

## Containers and isolation

With standalone Compose v2, use `docker-compose` for the same commands. Compose runs migrations before starting the API and workers. PostgreSQL stores originals and page images along with processing and review state. This keeps creation and deletion transactional at small scale. Storage throughput and vacuum behavior should be profiled before planning a larger workload.

The API and worker run as non-root users with dropped capabilities and read-only filesystems. The parser has a separate internal network, no database credentials, bounded tmpfs, and container limits. Each decode runs in a child process with an environment allowlist, a wall timeout, and Linux address-space and CPU limits. The parser API is an internal service. Keep it off public networks.

The checked-in Caddy configuration exposes HTTP on localhost. For shared deployment, place the gateway behind an HTTPS ingress, provide the secrets through the deployment environment, and keep database, metrics, and parser services private. Use a dedicated database role and tested backup, restore, and retention procedures appropriate to the deployment. The browser console stores the API key in per-tab session storage and clears it at disconnect.

## Observe and recover

- `/healthz` checks the API process. `/readyz` checks the database and a recent worker heartbeat.
- API metrics are available on the internal API port. Worker metrics on the internal worker port include stage duration and failures by stage and code.
- A worker can process one job at a time. Scale with `docker compose up -d --scale worker=2`. Parser capacity and the database connection budget limit safe concurrency.
- Expired leases become claimable. Transient parser failures retry with backoff. Invalid documents terminate with an error code. Reprocess starts a new generation.
- Delete cascades through the active database. PostgreSQL backups, WAL, and replicas have their own retention lifecycle.
- To revoke a key, set `api_keys.revoked_at` for the key digest in the database. Keep a record of which workspace and label the key was issued to.

## Verify

```sh
uv run ruff check workbench eval tests
uv run ruff format --check workbench eval tests
WORKBENCH_TEST_DATABASE_URL="$TEST_DATABASE_URL" uv run pytest -q
uv run python -m eval.runner --check
npm run build --prefix frontend
(cd frontend && npx playwright install chromium)
WORKBENCH_E2E_KEY="$WORKBENCH_API_KEY" npm run test:e2e --prefix frontend
```

The Playwright suite uses an isolated demo workspace and deletes its sample after export. Set `WORKBENCH_UI_URL` when testing the Caddy gateway.
