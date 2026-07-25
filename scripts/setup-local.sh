#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/apps/backend"
FRONTEND_DIR="${ROOT_DIR}/apps/frontend"

fail() {
    printf 'Error: %s\n' "$1" >&2
    exit 1
}

for command_name in uv node npm; do
    command -v "${command_name}" >/dev/null 2>&1 || fail "${command_name} is required. See SETUP.md."
done

node_major="$(node -p "process.versions.node.split('.')[0]")"
[ "${node_major}" = "24" ] || fail "Node.js 24 is required (found $(node --version))."

if [ ! -f "${BACKEND_DIR}/.env" ]; then
    cp "${BACKEND_DIR}/.env.example" "${BACKEND_DIR}/.env"
    printf 'Created apps/backend/.env from the safe local template.\n'
else
    printf 'Keeping existing apps/backend/.env unchanged.\n'
fi

printf 'Installing locked backend dependencies...\n'
(
    cd "${BACKEND_DIR}"
    uv sync --frozen
    if [ "$(uname -s)" = "Linux" ]; then
        printf 'Playwright may request sudo access for Chromium system libraries.\n'
        uv run playwright install --with-deps chromium
    else
        uv run playwright install chromium
    fi
)

printf 'Installing locked frontend dependencies...\n'
(
    cd "${FRONTEND_DIR}"
    npm ci --no-audit --no-fund
)

printf '\nSetup complete. Start the backend and frontend in two terminals:\n'
printf '  cd apps/backend && RELOAD=true uv run app\n'
printf '  cd apps/frontend && npm run dev\n'
printf 'Then open http://localhost:3000 and configure AI in Settings.\n'
