# FitWright — Developer Documentation

> Contributor reference for the FitWright codebase.

Generic, reusable guides (Swiss design system, Next.js performance) live in [`../portable/`](../portable/README.md) as standalone packs that can be lifted out of this repo and dropped into any project. In-depth system/subsystem design lives in [`../architecture/`](../architecture/). This index covers the project-specific guides tied to FitWright itself.

## Quick Navigation

### Core docs
| Doc | Purpose |
|-----|---------|
| [scope-and-principles](scope-and-principles.md) | Rules, what's in/out of scope |
| [quickstart](quickstart.md) | Install, run, test commands |
| [workflow](workflow.md) | Git, PRs, testing |
| [coding-standards](coding-standards.md) | Frontend/backend conventions |

### Architecture
| Doc | Purpose |
|-----|---------|
| [backend-architecture](architecture/backend-architecture.md) | Backend modules, API, services |
| [backend-guide](architecture/backend-guide.md) | Module-by-module backend tour |
| [frontend-architecture](architecture/frontend-architecture.md) | Components, pages, state |
| [frontend-workflow](architecture/frontend-workflow.md) | User flows in the frontend |

### System design (deep dives — `../architecture/`)
| Doc | Purpose |
|-----|---------|
| [ARCHITECTURE](../architecture/ARCHITECTURE.md) | System-shape constitution: rings, ports, deployment profiles |
| [PROFILE_SYSTEM_PLAN](../architecture/PROFILE_SYSTEM_PLAN.md) | Professional Profile data model and design |
| [PHOTO_SYSTEM](../architecture/PHOTO_SYSTEM.md) | Profile photo storage, rendering, and rules |
| [WYSIWYG_RENDERING](../architecture/WYSIWYG_RENDERING.md) | One-renderer preview = PDF export |
| [TEMPLATE_LIBRARY](../architecture/TEMPLATE_LIBRARY.md) | Metadata-driven resume template catalog |
| [PERSISTENT_ANALYSIS_CACHE](../architecture/PERSISTENT_ANALYSIS_CACHE.md) | Reuse of expensive AI results |

### APIs
| Doc | Purpose |
|-----|---------|
| [front-end-apis](apis/front-end-apis.md) | API contract |
| [api-flow-maps](apis/api-flow-maps.md) | Request/response flows |
| [backend-requirements](apis/backend-requirements.md) | Backend behavioral requirements |

### Design (FitWright specifics)
| Doc | Purpose |
|-----|---------|
| [template-system](design/template-system.md) | Resume template architecture |
| [pdf-template-guide](design/pdf-template-guide.md) | PDF rendering pipeline |
| [print-pdf-design-spec](design/print-pdf-design-spec.md) | Print/PDF design spec |
| [resume-template-design-spec](design/resume-template-design-spec.md) | Resume template design spec |
| [templates/swiss-single-spec](design/templates/swiss-single-spec.md) | Single-column Swiss template spec |
| [templates/swiss-two-column-spec](design/templates/swiss-two-column-spec.md) | Two-column Swiss template spec |

> **For the design system itself** (colors, components, anti-patterns), see the portable pack: [`../portable/swiss-design-system/`](../portable/swiss-design-system/README.md)

### Features
| Doc | Purpose |
|-----|---------|
| [custom-sections](features/custom-sections.md) | Dynamic sections |
| [resume-templates](features/resume-templates.md) | Template types and controls |
| [adding-resume-templates](features/adding-resume-templates.md) | How to add a new template |
| [enrichment](features/enrichment.md) | AI enrichment flow |
| [jd-match](features/jd-match.md) | Job description matching |
| [i18n](features/i18n.md) | Internationalization |

### LLM Integration
| Doc | Purpose |
|-----|---------|
| [llm-integration](llm-integration.md) | Multi-provider AI via LiteLLM |

### Portable packs (live outside this folder)
| Pack | Purpose |
|------|---------|
| [swiss-design-system](../portable/swiss-design-system/README.md) | Full Swiss style design system — required reading for frontend work |
| [nextjs-performance](../portable/nextjs-performance/README.md) | Next.js performance optimizations — required reading for frontend work |

## Project Structure

```
apps/
├── backend/                 # FastAPI + Python
│   ├── app/
│   │   ├── main.py          # Entry point
│   │   ├── routers/         # API endpoints
│   │   ├── services/        # Business logic
│   │   └── prompts/         # LLM templates
│   └── data/                # Database storage
│
└── frontend/                # Next.js + React
    ├── app/                 # Pages
    ├── components/          # UI components
    └── lib/                 # Utilities, API client
```

## How to Use

**New tasks:** Read `scope-and-principles` → `quickstart` → `workflow`

**Backend changes:** `backend-architecture` → `front-end-apis` → `llm-integration`

**Frontend changes:** `frontend-architecture` → portable [`swiss-design-system`](../portable/swiss-design-system/README.md) → portable [`nextjs-performance`](../portable/nextjs-performance/README.md) → `coding-standards`

**Template/PDF changes:** `pdf-template-guide` → `template-system`
