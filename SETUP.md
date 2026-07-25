# FitWright Local Setup

FitWright supports two local workflows:

1. **Docker Compose (recommended for users):** one container, one public port, no local Python or Node.js setup.
2. **Native development:** backend and frontend run separately with locked dependencies and hot reload.

AI credentials are optional during installation. The app, health check, and Settings page work without a key; configure a provider after startup.

## Option 1: Docker Compose (recommended)

### Prerequisites

- Git
- Docker Desktop, or Docker Engine with Docker Compose v2
- At least 4 GB of free memory and enough disk space for the image

### Install and start

```bash
git clone https://github.com/ObaidGits/FitWRight.git
cd FitWRight
docker compose up -d --build
```

The first build downloads dependencies and Chromium, so it takes longer than later starts. Check readiness with:

```bash
docker compose ps
docker compose logs -f fitwright
```

When the service is healthy, open:

| URL | Purpose |
|-----|---------|
| <http://localhost:3000> | Application |
| <http://localhost:3000/settings> | AI provider configuration |
| <http://localhost:3000/api/v1/health> | End-to-end health check |
| <http://localhost:3000/docs> | API documentation |

Stop or restart without deleting data:

```bash
docker compose down
docker compose up -d
```

> Do not add `-v` to `docker compose down` unless you intentionally want to delete all local FitWright data.

## Local configuration

The default Compose configuration is safe for one local user:

- `SINGLE_USER_MODE=true`; no email, OAuth, Redis, or hosted secrets required.
- Data is stored in the Docker volume `resume-data` and survives restarts/rebuilds.
- Cloud API keys are best entered in **Settings** after startup and are encrypted at rest.
- Local providers such as Ollama can run without a cloud key.

### Use Ollama from Docker

Start Ollama on the host, pull a model, then start FitWright with the host endpoint:

```bash
ollama pull gemma3:4b
LLM_PROVIDER=ollama LLM_MODEL=gemma3:4b \
  LLM_API_BASE=http://host.docker.internal:11434 \
  docker compose up -d --build
```

`host.docker.internal` is configured for Linux, macOS, and Windows by the Compose file.

### Use another host port

The public URL and host port must match:

```bash
PORT=4000 FRONTEND_BASE_URL=http://localhost:4000 docker compose up -d --build
```

Then open <http://localhost:4000>.

### Update an existing installation

Back up important data first, then rebuild without deleting the volume:

```bash
git pull
docker compose up -d --build
```

To inspect the volume name, run `docker volume ls`. Never copy or publish files from the volume because they can contain resumes and encrypted credentials.

## Option 2: Native development

### Prerequisites

| Tool | Required version |
|------|------------------|
| Python | 3.13 (managed automatically by `uv`) |
| Node.js | 24 (`apps/frontend/.nvmrc`) |
| npm | Version bundled with Node.js 24 |
| uv | Current release |
| Git | Any supported release |

On macOS, Linux, or WSL, run the non-destructive bootstrap script from the repository root:

```bash
bash scripts/setup-local.sh
```

The script:

- validates `uv`, Node.js 24, and npm;
- copies `apps/backend/.env.example` only when `.env` does not exist;
- installs Python dependencies from `uv.lock` with `--frozen`;
- installs Chromium and required Linux libraries for PDF export;
- installs frontend dependencies from `package-lock.json` with `npm ci`;
- never overwrites environment files or removes application data.

### Start native development

Start the backend in terminal 1:

```bash
cd apps/backend
RELOAD=true uv run app
```

Start the frontend in terminal 2:

```bash
cd apps/frontend
npm run dev
```

Open <http://localhost:3000>. The frontend proxies `/api`, `/docs`, `/redoc`, and `/openapi.json` to the backend at `127.0.0.1:8000`.

A frontend `.env.local` is not required for the default ports. For custom frontend settings, copy the template without replacing an existing file:

```bash
cd apps/frontend
[ -f .env.local ] || cp .env.sample .env.local
```

### Native quality checks

```bash
# Backend
cd apps/backend
uv sync --frozen --extra dev
uv run pytest -q

# Frontend
cd apps/frontend
npm ci
npm run lint
npm run test
npm run build
```

`pyproject.toml` and `uv.lock` are the authoritative backend dependency files. `package.json` and `package-lock.json` are authoritative for the frontend.

## Data and database behavior

Local mode uses SQLite and supporting files under `apps/backend/data/` (native) or `/app/backend/data/` in the Docker volume. Legacy TinyDB JSON is imported automatically when present.

Before moving or resetting data:

1. Stop FitWright.
2. Back up the entire data directory or Docker volume.
3. Keep `.secret_key` with the database; losing it can make stored provider keys unreadable.

There is intentionally no destructive reset command in this guide.

## Production is a separate mode

These local instructions use single-user mode. A hosted multi-user deployment must be built with `NEXT_PUBLIC_SINGLE_USER_MODE=false`, run with `SINGLE_USER_MODE=false`, and configure Postgres plus stable security secrets. Do not expose the local Compose configuration directly to the internet.

## Troubleshooting

### Container is not healthy

```bash
docker compose ps
docker compose logs --tail=200 fitwright
```

Verify that port 3000 is free, then retry `docker compose up -d --build`. A cloud API key is not required for the service to become healthy.

### Port 3000 is already in use

```bash
PORT=4000 FRONTEND_BASE_URL=http://localhost:4000 docker compose up -d --build
```

### Backend dependency mismatch

Do not delete the lockfile. Restore it and run:

```bash
cd apps/backend
uv sync --frozen
```

### Frontend dependency mismatch

Use the committed lockfile:

```bash
cd apps/frontend
npm ci
```

### Native PDF export fails on Linux

Install Playwright's Chromium and system dependencies:

```bash
cd apps/backend
uv run playwright install --with-deps chromium
```

### Ollama connection fails

Confirm Ollama is running and the model exists:

```bash
ollama list
```

Native development uses `http://localhost:11434`; Docker uses `http://host.docker.internal:11434`.

### AI generation reports a missing key

The installation is working; only the selected cloud provider is unconfigured. Open <http://localhost:3000/settings>, save the provider key, and use **Test Connection**.

## Getting help

When reporting a setup issue, include your operating system, Docker/Node/Python version, the command that failed, and sanitized logs. Never post `.env` contents, API keys, resumes, database files, or `.secret_key`.
