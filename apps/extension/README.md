# FitWright Companion (Browser Extension)

A Chrome extension that brings FitWright to the pages where job hunting actually
happens. Save a job from any posting, autofill an application from your master
resume, and see your match score before you spend twenty minutes on a form.

## Why an extension

FitWright's server-side job discovery cannot reach every board. Instahyre sits
behind a Cloudflare challenge, Foundit behind an Akamai WAF, and both gate
results behind a login — none of which a datacenter IP gets past. Running in your
own browser solves all three at once: a residential IP, a real browser
fingerprint, and the sessions you are already signed in to.

That same position unlocks two things a server never could: filling application
forms on your behalf, and scoring a job against your resume while you read it.

## Setup

**The app walks you through this**: open FitWright and go to
**Setup → Browser extension** (`/setup/extension`). That page names the exact
folder to load, says whether it has been built yet, and turns green by itself the
moment the extension is running - no guessing whether it worked.

Chrome has no API that installs an unpacked extension, and it dropped
double-click `.crx` installs years ago, so the steps below cannot be automated
away. Only the Chrome Web Store gives a one-click install; publishing there
changes the extension ID, which has to be updated in `EXTENSION_ORIGINS` at the
same time or the backend will reject the new build.

```bash
cd apps/extension
npm install
npm run build
```

Then load it:

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and select `apps/extension/dist`.
3. Copy the extension ID Chrome now shows on that card.

Point the backend at it — the extension calls the API with cookies, so its origin
must be allowed explicitly. In `apps/backend/.env`:

```env
JOB_DISCOVERY=true
EXTENSION_ORIGINS=chrome-extension://<the-id-you-copied>
```

Restart the backend. An unpacked build gets a **different ID on every machine**,
which is why this is not hardcoded.

Finally, open the extension's options page (it opens itself on first install) and:

- confirm the FitWright URL (default `http://localhost:3000`),
- click **Test connection** — it should report a resume was found,
- fill in the answers your resume cannot supply: work authorization, notice
  period, salary expectation.

You must be signed in to FitWright in a normal tab. The extension stores no
password and holds no token; it rides your existing session, so signing out of
FitWright signs the extension out too.

## Searching from the web app

The Discovery page routes boards it cannot reach itself to this extension. Four
boards are extension-only — **Instahyre, Hirist, Foundit and YC Startups** — and
they are marked with a puzzle icon in the platform picker. Tick one and search:
the extension opens each board in a background tab, harvests the results, posts
them to your feed and closes the tab. The page reports what it added
("Extension added 20 jobs: hirist 20"); everything else in the picker is still
searched by the server, and both lanes run in parallel.

With the extension not installed, those boards show an install prompt instead of
failing silently, and the server-side boards keep working.

How the two halves talk: a content script (`src/content/bridge.ts`) is injected
only on the FitWright origins in the manifest and relays `window.postMessage`
envelopes to the service worker. The page cannot call `chrome.runtime` directly,
and no session, token or profile data is ever passed out to the page — only
scrape requests in and counts back.

## What it does

| Feature | Where it appears |
| --- | --- |
| **Save a job** | Popup, or the badge's Save button, on any supported posting |
| **Match score** | Floating badge on job pages: score, matched skills, gaps |
| **Autofill** | Popup, on ATS application forms |
| **AI answer drafting** | Runs with autofill for open-ended questions |
| **Bulk scrape** | Popup, on a search results page |
| **Search from the web app** | Discovery page, for the extension-only boards |
| **Scheduled scraping** | Background tabs on a timer, configured in options |
| **Applied tracking** | Automatic — detects submission, marks the job applied |

### It fills forms. It never submits them.

Autofill stops after populating fields. You review everything and click submit
yourself. This is deliberate: an unreviewed AI-drafted answer reaching a real
employer is not a tradeoff worth making, and auto-submission is also what gets
extensions pulled from the Web Store and flagged as spam by ATS platforms.

Autofill also never overwrites a field you already typed into, so re-running it
after manual edits is safe.

## Supported sites

**ATS (application forms):** Greenhouse, Lever, Ashby, Workday, SmartRecruiters

**Boards (discovery):** Indeed, LinkedIn, Instahyre, Hirist, Foundit, YC Startups
(Work at a Startup), Naukri, ZipRecruiter, Glassdoor, Google Jobs

Eight of those are **extension-only** — Instahyre, Hirist, Foundit, YC, Naukri,
ZipRecruiter, Glassdoor and Google all refuse the server (Cloudflare, an Akamai
WAF, a recaptcha, or an empty response) and work from your browser instead.

> **Google Jobs note:** the extension only runs on `google.com/search`, and only
> acts on the jobs surface (`&udm=8`) — an ordinary Google search is classified
> and ignored. Google also serves a captcha to browsers it distrusts; in your own
> signed-in profile it renders normally, but if it ever walls you the board
> simply reports no results.

Anything else with schema.org `JobPosting` markup — which most company career
pages have, because Google for Jobs requires it — works through the generic
adapter. Site-specific adapters are only there to do better than generic on the
sites that matter most.

## Architecture

```
src/
  lib/            Shared, context-agnostic
    types.ts        Wire types mirroring the backend router
    messages.ts     Typed message bus (a bad message name is a compile error)
    api.ts          FitWright client - service worker only
    storage.ts      chrome.storage wrapper
    dom.ts          Extraction + framework-safe value setting
    fields.ts       Form field -> profile key heuristics
  adapters/       One per platform; pure DOM readers, no network, no storage
    generic.ts      schema.org JSON-LD - the highest-coverage adapter
    ats.ts          Greenhouse, Lever, Ashby, Workday, SmartRecruiters
    boards.ts       Indeed, LinkedIn, Instahyre, Hirist, Foundit
    registry.ts     Ordered lookup, generic last
  background/     Service worker: all network calls, alarms, toolbar badge
  content/        Injected: page classification, badge UI, autofill, tracking
  popup/ options/ Extension UI
```

Three decisions worth knowing:

**All network calls live in the service worker.** A content script runs on the
job site's origin, so its `fetch` to FitWright is cross-origin and subject to
that site's CSP — which job boards set strictly. It also means the FitWright
session is never reachable from a third-party page's JavaScript.

**Adapters own no logic beyond extraction.** When a site redesigns — and they all
do — the damage is one file's selectors, and a broken adapter falls through to
generic rather than breaking capture.

**The autofill profile is derived, not stored.** Name, contact and experience come
from your resume on every request, so there is no second copy to drift out of
sync. Only the answers a resume cannot contain live in extension storage.

## Backend contract

Seven routes under `/api/v1/extension`, implemented in
`apps/backend/app/routers/extension.py`:

| Route | Purpose |
| --- | --- |
| `GET /ping` | Handshake: API version, resume presence |
| `GET /profile` | Autofill profile derived from your master resume |
| `POST /capture` | Save one job to the discovery feed |
| `POST /scrape` | Bulk-ingest a scraped batch (max 200) |
| `POST /match` | Score a job description against your resume |
| `POST /draft` | Draft an application answer |
| `POST /applied` | Mark a job applied |

The whole surface is gated on `JOB_DISCOVERY`. With it off, every route 404s and
leaks nothing about what exists behind it.

`API_VERSION` in `src/lib/types.ts` must match `EXTENSION_API_VERSION` in the
router. The handshake compares them so a stale build warns up front instead of
failing confusingly halfway through an autofill.

## Development

```bash
npm run dev        # esbuild watch
npm run typecheck  # tsc --noEmit
npm run build      # production bundle -> dist/
npm run package    # zip dist/ for the Web Store
```

After a rebuild, hit **Reload** on the extension card in `chrome://extensions`.
Content script changes also need a refresh of the job page itself.

Debugging:

- **Service worker** — `chrome://extensions` → *Inspect views: service worker*.
  It is ephemeral and gets killed after ~30s idle; that is normal, not a crash.
- **Content script** — the job page's own DevTools console.
- **Popup** — right-click the toolbar icon → *Inspect popup*.

### Adding a site

1. Write an adapter in `src/adapters/` implementing `SiteAdapter`.
2. Register it in `registry.ts` (before `generic`).
3. Add the host to **both** `host_permissions` and `content_scripts.matches` in
   `public/manifest.json`.
4. If it should be background-scrapeable, add a URL shape to `searchUrlFor()`.

## Privacy

Data leaves your browser in exactly two directions: to your own FitWright
instance, and nowhere else. There is no analytics, no telemetry, and no third
party endpoint. `chrome.storage.sync` holds your settings and the application
answers you typed into the options page; `chrome.storage.local` holds caches
(match scores, which jobs were already captured) that are safe to lose.

Scraping is rate-limited by design: background runs open one tab at a time with a
settle delay, because a burst of parallel tabs both spikes memory and looks
nothing like a person browsing.
