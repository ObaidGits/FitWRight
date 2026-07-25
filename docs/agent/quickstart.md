# Quickstart Guide

> Essential commands to install, run, and test FitWright.

## User installation

Docker Compose is the canonical local path:

```bash
docker compose up -d --build
docker compose ps
```

Open <http://localhost:3000> after the service becomes healthy. Configure AI at <http://localhost:3000/settings>; a provider key is not required for startup.

## Native developer setup

Requires Node.js 24 and `uv` with Python 3.13:

```bash
bash scripts/setup-local.sh
```

Then run in separate terminals:

```bash
cd apps/backend && RELOAD=true uv run app
cd apps/frontend && npm run dev
```

## Quality checks

```bash
cd apps/backend
uv sync --frozen --extra dev
uv run pytest -q

cd apps/frontend
npm ci
npm run lint
npm run test
npm run build
```

Environment templates are `apps/backend/.env.example` and `apps/frontend/.env.sample`. Setup never overwrites real environment files or local data. See [SETUP.md](../../SETUP.md) for Ollama, custom ports, and troubleshooting.
