# WYSIWYG Resume Rendering — One Renderer, Preview = Export

The resume preview and the exported PDF are produced by a single renderer, so
what the user sees on screen matches the downloaded document. This document
describes how that is achieved and the known fidelity boundaries.

---

## 1. The rendering pipeline

The PDF is produced by headless Chromium loading a real frontend page and
snapshotting it:

```
GET /resumes/{id}/pdf                     (apps/backend/app/routers/resumes.py)
  → builds URL: {frontend}/print/resumes/{id}?<template settings as query>
  → render_resume_pdf(url, pageSize, margins)          (apps/backend/app/pdf.py)
      → Chromium goto → wait .resume-print + fonts + images → page.pdf(format, margins)
```

The print page renders the canonical `Resume` renderer:

```
/print/resumes/[id]  →  <div class="resume-print"><Resume settings={marginsZeroed} .../>
```

Key fact: **margins are applied by Playwright as real PDF page margins**, so the
print DOM zeroes CSS margins; Chromium lays the page out in **CSS pixels at 96
DPI** (A4 = 793.7 × 1122.5px), and the content box = page − margins.

### Divergences this design eliminates

The following are the classes of preview/PDF mismatch the single-renderer design
prevents:

| # | Divergence | Why it caused "surprise after download" |
| --- | --- | --- |
| A | Preview used a **separate wrapper** (`RenderTemplate`) with its own template→component switch; PDF used `Resume`. Two mappings drift. | Section/label/i18n differences |
| B | Preview rendered margins as **CSS padding** on a non-page element; PDF applies margins as real page margins. | Different margin geometry |
| C | Preview was **one infinite scroll at the panel's arbitrary width**; the PDF lays out at the A4 content-box width and paginates. | **Line wrapping and page breaks differed — the #1 surprise** |
| D | Preview didn't pass localized headings. | i18n mismatch |

The template *body* components (`ResumeSingleColumn`, `ResumeTwoColumn`, …) were
already shared — the drift was entirely in the wrappers and the page geometry.

---

## 2. Design — one renderer, real page geometry

**There is now one resume renderer.** The preview delegates to the exact same
canonical `Resume` component the PDF path uses; the separate preview template
switch is gone.

```
Preview  →  RenderTemplate  →  ResumeDocument  ─┐
                                                ├─► Resume (canonical) ─► template body
PDF/Print →  /print/resumes/[id]  ─────────────┘        (single source of truth)
```

`ResumeDocument` (`components/resume/resume-document.tsx`) reproduces the PDF
geometry so the preview is WYSIWYG:

- **Exact content width.** Content is rendered at `page − margins` in CSS px at
  96 DPI — the same box Chromium uses — so **line wrapping and horizontal layout
  match the PDF** (given the same fonts). This closes divergence C/E.
- **Margins as page padding.** The inner `Resume` renders with margins zeroed
  (exactly like the print page); the page surface supplies the margins as
  padding — the same model Playwright uses. Closes divergence B.
- **Real A4/Letter pages, not infinite scroll.** Content is laid across page
  surfaces with visible boundaries and page shadows.
- **Scale the canvas, never reflow.** The whole page canvas is scaled to fit its
  container (`fitScale`, never upscales) via a `ResizeObserver`, so the document
  layout is identical at any zoom / screen size.
- **Unified i18n.** The canonical `Resume` / template components self-localize
  via the client `useTranslations`, matching the print page's labels. Closes D.

### Page-break engine (`lib/resume/pagination.ts`, pure + unit-tested)

- Geometry: `mmToPx`, `pageWidthPx/HeightPx`, `contentWidthPx`,
  `pageContentHeightPx`, `fitScale`.
- `computePageOffsets(blocks, pageContentHeight)` mirrors the PDF break rules:
  it opens a new page *before* any measured flow block that would overflow —
  **never splitting a block** (`break-inside: avoid`) — and glues section titles
  to their first item via `groupFlowBlocks` so a title is never orphaned
  (`break-after: avoid`). An oversized block (taller than a page) is clipped
  rather than looping forever, matching a forced break.
- The component measures flow blocks (header, sections, items) from a hidden
  measurement copy at the exact content width, using the shared CSS-module class
  names resolved at runtime (no per-template edits), then renders each page as a
  clipped window over the single document translated to that page's offset.

### Accessibility

Page 1's DOM already contains the **complete** document (visually clipped to its
slice), so pages 2+ are pure visual duplicates and are marked `aria-hidden` +
`inert` — assistive tech and keyboard navigation traverse the resume exactly
once. The measurement copy is `aria-hidden`. Scaling is transform-based (honors
browser zoom); no fabricated layout.

---

## 3. Tests

- `tests/resume-pagination.test.ts` — geometry (mm→px, A4/Letter, content box),
  `fitScale`, `groupFlowBlocks` (break-after), and `computePageOffsets`
  (fits-on-one-page, overflow opens a page, title-stays-with-item,
  oversized-no-loop, unmeasured→single page, page count).
- `tests/resume-document.test.tsx` — renders the unified content on a real page
  surface (A4 + Letter).
- Backend PDF path contract: `test_pdf_render.py`, `export-pdf-url`.

---

## 4. Fidelity boundaries

- **Item-level page breaks are exact for single-column templates.** For
  **two-column** templates the two columns are laid side by side, so measured
  break positions (document-order) are a close approximation, not exact. The
  width/margins/scaling are exact for every template.
- **System-font stacks.** Preview uses the *viewer's* system fonts; the PDF uses
  the *render host's* system fonts (both `ui-serif` / `ui-sans-serif` stacks).
  On machines with different system fonts, glyph metrics can differ slightly.
  Absolute cross-machine parity requires embedding webfonts (a bundled `@font-face`
  used by both the preview and the print page) — the recommended next step.
- **Public profile / portfolio** (`/p/[slug]`) render `PublicProfileView` from
  the **profile projection**, a different data model than `ResumeData` — they are
  a distinct product surface, not the resume renderer, so they are intentionally
  out of this unification.
