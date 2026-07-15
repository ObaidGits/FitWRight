# Resume Template Library — Metadata-Driven Template System

A browsable catalog of resume templates built as curated presets over a small
set of proven layout engines, sharing the single WYSIWYG renderer so preview,
PDF, print, and export stay consistent with zero duplicated layout logic.

---

## 1. Underlying infrastructure

The template system builds on the following existing pieces:

- **7 layout engines** (`TemplateType`): swiss-single, swiss-two-column, modern,
  modern-two-column, latex, clean, vivid.
- **One renderer** (`Resume` / the WYSIWYG `ResumeDocument`) shared by preview +
  PDF + print (see `WYSIWYG_RENDERING.md`).
- **`TemplateSettings`** (template, pageSize, margins, spacing, fontSize {base,
  headerScale, headerFont, bodyFont}, compactMode, showContactIcons,
  accentColor) + `settingsToCssVars` + `applyTemplatePreset`.
- **Photo capabilities** per engine (`template-capabilities.ts`) — templates are
  photo-aware, not photo-coupled; the renderer adapts automatically.
- **PDF export** driven entirely by `TemplateSettings` query params.

What was missing was the *system on top*: a browsable catalog with metadata,
categories, ATS scores, recommendations, and a gallery.

## 2. Design — a template is metadata + a preset (no new renderers)

A catalog template is **not** a new rendering component. It is a curated
**preset over an existing engine** plus structured metadata:

```
ResumeTemplate = {
  id, name, category, engine (TemplateType),
  settings: Partial<TemplateSettings>,   // accent, fonts, spacing, density, page
  photoSupport: 'none' | 'supported' | 'required', photoPosition,
  atsScore (1–5) + atsNote, industries, experienceLevels, countries,
  recommendedFor, tags, popularity, version, description,
}
```

`templateToSettings(template)` composes the preset over the engine defaults (via
`applyTemplatePreset`, which seeds signature fonts for single-typeface engines)
into a full `TemplateSettings` — the **same** shape the shared renderer and the
PDF export already consume. So preview, PDF, print, and export are automatically
consistent, with **zero duplicated layout logic**.

**Scalability contract (met):** adding a template = appending one entry to
`RESUME_TEMPLATES`. No changes to rendering, PDF, preview, or photo code. A test
enforces that every entry maps to a real engine and that photo support is
consistent with the engine's capabilities.

Why presets over engines rather than 25 bespoke CSS layouts: it is exactly how
premium builders (e.g. Reactive Resume) scale — a few proven, ATS-tested layout
engines themed into many purposeful templates — and it keeps every template on
the single audited renderer instead of 25 drift-prone stylesheets.

## 3. Components

- **Catalog** (`lib/resume/template-catalog.ts`): **26 templates** across ATS,
  Professional, Technology, Creative, Academic, Career-stage, and International
  categories, spanning all 7 engines with photo and no-photo variants, each with
  an honest ATS score + reason. Plus pure `filterTemplates`, `sortTemplates`,
  `templateToSettings`, `getTemplateById`, `photoSupportIsConsistent`.
- **Recommendations** (`lib/resume/template-recommend.ts`): pure, transparent
  ranking from a light signal (role, industry, experience level, skills,
  country) derived from the user's resume (`signalFromResume`,
  `experienceLevelFromResume`). Every recommendation carries a human reason. No
  LLM call.
- **Gallery** (`components/resume/template-gallery.tsx`): search, category +
  photo + ATS + favorites filters, sort (recommended / popular / ATS / name),
  favorites (localStorage), a personalized "Recommended" badge, ATS star
  ratings, photo badges, and a preview dialog. **Every thumbnail is a real
  render** through `ResumeDocument` (page 1), lazily mounted via
  `IntersectionObserver` for performance; thumbnails are `inert`/non-interactive.
- **`ResumeDocument` `maxPages`** prop added for 1-page thumbnails / 2-page
  previews.
- **Route + integration**: `/templates` gallery page (personalized from the
  master resume), a cross-flow **preferred-template bridge**
  (`lib/resume/preferred-template.ts`, localStorage) so selecting a template
  opens the **wizard already rendered in that template** (with a "Change
  template" link), and a "Browse templates" entry on the Import page.

## 4. Tests

- `tests/template-catalog.test.ts` — unique URL-safe ids, every engine valid,
  ATS 1–5 with reasons, photo/engine consistency, category + photo coverage,
  `templateToSettings` composition, filter/sort, and recommendation ranking
  (SWE → software-engineer, fresher → student templates, role match outscores
  unrelated).
- `tests/template-gallery.test.tsx` — renders cards + count, no-photo filter,
  free-text search, `onSelect`, and the preferred-template bridge round-trip /
  unknown-id fallback.

## 5. Scope boundaries

- **Bespoke layout engines** (e.g. a true sidebar-photo executive layout) for
  looks the current 7 engines can't express would be added additively, each with
  PDF parity and tests.
- **Template versioning/migration**: the `version` field exists, but a migration
  runner (upgrading a saved resume when a template's preset changes) is not
  implemented.
- **Two-column pagination fidelity** and **embedded webfonts** are shared
  limitations documented in `WYSIWYG_RENDERING.md`.
- **Public profile / portfolio** surfaces render the profile projection (a
  different data model) and are intentionally out of scope for the resume
  template system.
