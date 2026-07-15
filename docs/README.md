# FitWright Documentation

Technical documentation for contributors and maintainers. For installation and a
product overview, start with the repository [`README.md`](../README.md) and
[`SETUP.md`](../SETUP.md).

## Where to look

| Area | Location | What it covers |
|------|----------|----------------|
| **Developer guide** | [`agent/`](agent/README.md) | Getting oriented, coding standards, architecture tours, API contracts, feature guides, testing strategy |
| **System design** | [`architecture/`](architecture/) | In-depth design of core subsystems (system shape, profile system, photo pipeline, rendering, template library, analysis cache) |
| **Design system** | [`portable/swiss-design-system/`](portable/swiss-design-system/README.md) | Swiss International Style tokens, components, layouts, anti-patterns |
| **Frontend performance** | [`portable/nextjs-performance/`](portable/nextjs-performance/README.md) | Next.js performance patterns and pre-PR checklist |

## Quick starts

- **New contributor** → [`agent/scope-and-principles`](agent/scope-and-principles.md) → [`agent/quickstart`](agent/quickstart.md) → [`agent/workflow`](agent/workflow.md)
- **Backend work** → [`agent/architecture/backend-architecture`](agent/architecture/backend-architecture.md) → [`agent/apis/front-end-apis`](agent/apis/front-end-apis.md) → [`agent/llm-integration`](agent/llm-integration.md)
- **Frontend work** → [`agent/architecture/frontend-architecture`](agent/architecture/frontend-architecture.md) → [`portable/swiss-design-system`](portable/swiss-design-system/README.md) → [`portable/nextjs-performance`](portable/nextjs-performance/README.md)
- **System design** → [`architecture/ARCHITECTURE.md`](architecture/ARCHITECTURE.md)

## Contributing, security, license

Contribution and policy documents live under [`.github/`](../.github/): the
[Contributing guide](../.github/CONTRIBUTING.md), [Security policy](../.github/SECURITY.md),
and [Code of Conduct](../.github/CODE_OF_CONDUCT.md). The project is licensed under
[Apache 2.0](../LICENSE).
