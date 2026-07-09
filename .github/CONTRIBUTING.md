# Contributing to FitWright

Thank you for taking the time to contribute to [FitWright](https://github.com/ObaidGits/FitWRight).

We want you to have a great experience making your first contribution. This
contribution could be anything from fixing a typo in the documentation to
shipping a full feature.

If you would like to contribute but don't know where to start, check the issues
labeled `good first issue` or `help wanted`.

The development branch is `main`. All pull requests should target this branch.

## Reporting Bugs

Please try to create bug reports that are:

- **Reproducible.** Include steps to reproduce the problem.
- **Specific.** Include as much detail as possible: which version, what environment, etc.
- **Unique.** Do not duplicate existing open issues.
- **Scoped to a single bug.** One bug per report.

## Development Setup

FitWright is a monorepo with a Python FastAPI backend and a Next.js frontend.
Full setup instructions live in [SETUP.md](../SETUP.md). In short:

1. Fork the repository [here](https://github.com/ObaidGits/FitWRight/fork).

2. Clone your fork:

   ```bash
   git clone https://github.com/<YOUR-USERNAME>/FitWRight.git
   cd FitWRight
   ```

3. Start the backend (Terminal 1):

   ```bash
   cd apps/backend
   cp .env.example .env
   uv sync
   uv run app
   ```

4. Start the frontend (Terminal 2):

   ```bash
   cd apps/frontend
   npm install
   npm run dev
   ```

Open <http://localhost:3000> and configure your AI provider in Settings.

## Testing

Please test your changes before submitting a PR.

- Backend: `cd apps/backend && uv run pytest`
- Frontend: `cd apps/frontend && npm run test`

Also run the linters/formatters:

- Frontend: `npm run lint` and `npm run format`

## Pull Requests

Pull Requests and Issues are welcome. When opening a PR:

- Describe what changed and why.
- Note what you tested.
- Keep the scope focused.

Spot a problem? [Open an issue](https://github.com/ObaidGits/FitWRight/issues).
Have an idea? Start a [discussion](https://github.com/ObaidGits/FitWRight/discussions).
