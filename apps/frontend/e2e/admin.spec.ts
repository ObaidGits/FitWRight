import { test, expect } from '@playwright/test';

/**
 * Admin console E2E smoke (P2 Admin, Task 9.2).
 *
 * Quota-independent navigation + real-data smoke against the live stack
 * (frontend :3000 + backend :8000). In the default local `SINGLE_USER_MODE` the
 * bootstrap owner is an admin, so `/admin/*` is reachable without a login wall
 * and every page hydrates from the real `/api/v1/admin/*` endpoints (no mocks).
 *
 * The AI-native product path is unrelated here - these steps never call an LLM,
 * so the suite stays green without provider quota.
 */

test.describe('FitWright - admin console smoke', () => {
  test('overview renders KPI cards + the windowed usage chart', async ({ page }) => {
    await page.goto('/admin');
    await expect(page.getByRole('heading', { name: /Overview/i })).toBeVisible();
    // KPI cards from GET /admin/kpis.
    await expect(page.getByText(/Total users/i)).toBeVisible();
    await expect(page.getByText(/New users today/i)).toBeVisible();
    await expect(page.getByText(/Error rate \(24h\)/i)).toBeVisible();
    // The former Analytics chart is folded into Overview: metric + window selectors.
    await expect(page.getByLabel(/Metric/i)).toBeVisible();
    await expect(page.getByLabel(/Time window/i)).toBeVisible();
  });

  test('users page loads the real list with search + filters', async ({ page }) => {
    await page.goto('/admin/users');
    await expect(page.getByRole('heading', { name: /^Users$/i })).toBeVisible();
    await expect(page.getByLabel(/Search users/i)).toBeVisible();
    await expect(page.getByLabel(/Status filter/i)).toBeVisible();
    // Search syncs to the URL (shareable / back-button safe).
    await page.getByLabel(/Search users/i).fill('owner');
    await expect(page).toHaveURL(/q=owner/, { timeout: 5_000 });
  });

  test('audit page renders the append-only trail', async ({ page }) => {
    await page.goto('/admin/audit');
    await expect(page.getByRole('heading', { name: /Audit log/i })).toBeVisible();
    await expect(page.getByLabel(/Filter by event/i)).toBeVisible();
  });

  test('admin nav links move between sections', async ({ page }) => {
    await page.goto('/admin');
    await page.getByRole('link', { name: /^Users$/ }).click();
    await expect(page).toHaveURL(/\/admin\/users/);
    await page.getByRole('link', { name: /^Audit$/ }).click();
    await expect(page).toHaveURL(/\/admin\/audit/);
  });
});

/**
 * Admin console - observability + product-usage walkthrough (Tasks 19.1 / 19.2).
 *
 * Extends the smoke suite above with the full operator journey across the five
 * observability sections plus the read-only Configuration tab, the manage-only
 * Maintenance panel, and the Product-usage analytics section. Every assertion
 * checks STRUCTURE / LABELS (headings, regions, column headers, control names)
 * rather than concrete values, so the suite is resilient to real-data variance
 * and never depends on LLM/provider quota. All pages hydrate from the real
 * `/api/v1/admin/*` endpoints against the live stack.
 *
 * Accessible headings by page (verified): Overview -> "Overview";
 * Health -> "System Health"; AI -> "AI Analytics"; Storage -> "Storage";
 * Audit -> "Audit log". The sidebar/mobile nav link labels are the shorter
 * "Overview - Health - Users - AI - Storage - Audit".
 */
test.describe('FitWright - admin observability walkthrough (19.1)', () => {
  test('navigation walks Overview -> Health -> AI -> Storage -> Audit', async ({ page }) => {
    await page.goto('/admin');
    await expect(page.getByRole('heading', { name: /^Overview$/i })).toBeVisible();

    await page.getByRole('link', { name: /^Health$/ }).click();
    await expect(page).toHaveURL(/\/admin\/health/);
    await expect(page.getByRole('heading', { name: /System Health/i })).toBeVisible();

    await page.getByRole('link', { name: /^AI$/ }).click();
    await expect(page).toHaveURL(/\/admin\/ai/);
    await expect(page.getByRole('heading', { name: /AI Analytics/i })).toBeVisible();

    await page.getByRole('link', { name: /^Storage$/ }).click();
    await expect(page).toHaveURL(/\/admin\/storage/);
    await expect(page.getByRole('heading', { name: /^Storage$/i })).toBeVisible();

    await page.getByRole('link', { name: /^Audit$/ }).click();
    await expect(page).toHaveURL(/\/admin\/audit/);
    await expect(page.getByRole('heading', { name: /Audit log/i })).toBeVisible();
  });

  test('health page renders tiles, release fields and the jobs table', async ({ page }) => {
    await page.goto('/admin/health');
    await expect(page.getByRole('heading', { name: /System Health/i })).toBeVisible();

    // Subsystem tiles region (each tile pairs a text status label with colour).
    await expect(page.getByRole('region', { name: /Subsystem health tiles/i })).toBeVisible({
      timeout: 15_000,
    });

    // Release / deployment fields - version + environment labels are always shown.
    await expect(page.getByRole('heading', { name: /^Release$/i })).toBeVisible();
    await expect(page.getByText(/^Version$/i)).toBeVisible();
    await expect(page.getByText(/^Environment$/i)).toBeVisible();

    // Background-jobs table with the last-success + stuck-aware columns (Req 8.4).
    await expect(page.getByRole('heading', { name: /Background jobs/i })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: /Last success/i })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: /^State$/i })).toBeVisible();
  });

  test('overview shows KPI cards + window selector and Refresh works', async ({ page }) => {
    await page.goto('/admin');
    // KPI cards (Req 13.8) - labelled values from GET /admin/kpis.
    await expect(page.getByText(/Total users/i)).toBeVisible();
    await expect(page.getByText(/AI calls today/i)).toBeVisible();
    await expect(page.getByText(/Purge backlog/i)).toBeVisible();

    // Windowed usage chart selectors (metric + time window).
    await expect(page.getByLabel(/Metric/i)).toBeVisible();
    await expect(page.getByLabel(/Time window/i)).toBeVisible();

    // Refresh re-fetches without crashing; the KPI region stays present after.
    await page
      .getByRole('button', { name: /Refresh/i })
      .first()
      .click();
    await expect(page.getByText(/Total users/i)).toBeVisible();
  });

  test('configuration tab is strictly read-only (no edit/save/input controls)', async ({
    page,
  }) => {
    await page.goto('/admin/health');
    await page.getByRole('tab', { name: /Configuration/i }).click();

    // The tab announces itself as read-only.
    await expect(page.getByText(/Read-only/i)).toBeVisible({ timeout: 15_000 });

    // No mutating affordances anywhere in the (now-active) config content:
    // no Save / Edit / Delete buttons and no editable inputs. (Refresh is a
    // read-only re-fetch, not a config edit, so it is allowed.)
    await expect(page.getByRole('button', { name: /^save$/i })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^edit/i })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^delete/i })).toHaveCount(0);
    await expect(page.getByRole('textbox')).toHaveCount(0);
  });

  test('maintenance actions are visible to a manage admin and return a started/running result', async ({
    page,
  }) => {
    await page.goto('/admin/health');
    await page.getByRole('tab', { name: /Configuration/i }).click();

    // The manage-only Maintenance panel + its four idempotent job actions.
    await expect(page.getByRole('heading', { name: /^Maintenance$/i })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole('button', { name: /Refresh metrics/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Run rollup/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Run cleanup/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Run retention/i })).toBeVisible();

    // Trigger one action; the aria-live status region reports a started /
    // already-running / disabled outcome (all non-error). Be resilient: the
    // action re-invokes a real job, so assert on the outcome shape, not a value.
    await page.getByRole('button', { name: /Run rollup/i }).click();
    const status = page.getByRole('status');
    await expect(status).toContainText(/started|already running|disabled/i, { timeout: 20_000 });
    await expect(status).not.toContainText(/failed/i);
  });

  test('product-usage section is distinct from the observability KPIs', async ({ page }) => {
    await page.goto('/admin');

    // Observability KPIs live in their own "Key metrics" region...
    await expect(page.getByRole('region', { name: /Key metrics/i })).toBeVisible({
      timeout: 15_000,
    });

    // ...while product analytics live in a separate "Product usage" region with
    // its own heading (feature adoption, not platform health).
    const productUsage = page.getByRole('region', { name: /Product usage/i });
    await expect(productUsage).toBeVisible();
    await expect(productUsage.getByRole('heading', { name: /Product usage/i })).toBeVisible();
    // The resume-analytics sub-panel lives in the same product-usage section.
    await expect(productUsage.getByRole('heading', { name: /Resume analytics/i })).toBeVisible();
  });
});

/**
 * Admin console - negative, accessibility and mobile checks (Task 19.2).
 *
 * Covers the chart data-table fallback, keyboard operability, a 320-767px
 * no-horizontal-scroll sweep, and the error -> retry recovery path (forced via
 * request interception so it is deterministic without breaking the stack).
 *
 * GAP - non-admin blocked / read-only admin (Req 11.5 / 11.6): the local
 * `SINGLE_USER_MODE` stack has exactly ONE user, the bootstrap owner, who is a
 * manage-capable admin. There is therefore no second, non-admin (or read-only
 * admin) session to drive a true "non-admin is blocked" or "read-only admin sees
 * no manage controls" journey end-to-end in this harness. We document that here
 * and instead assert the capability-gated controls DO render for the manage
 * admin (below + in the 19.1 maintenance test). Server-side enforcement of the
 * admin.read-vs-admin.manage boundary is covered by the backend authz-matrix
 * test (task 18.1); the client `isAdmin` gate is defense-in-depth UX only.
 */
test.describe('FitWright - admin a11y + mobile + negative (19.2)', () => {
  test('usage chart exposes an accessible title and a data-table fallback', async ({ page }) => {
    await page.goto('/admin');
    // The metric/window controls anchor the usage chart region.
    await expect(page.getByLabel(/Metric/i)).toBeVisible({ timeout: 15_000 });

    // The UsageChart renders an SVG with role="img" + accessible title AND an
    // sr-only <table> fallback. With no data it shows an explicit empty state
    // instead - accept either so the assertion is resilient to real data.
    const chart = page.getByRole('img', { name: /over the last \d+ days/i });
    const chartTable = page.getByRole('table', { name: /over the last \d+ days/i });
    const emptyState = page.getByText(/in this window yet/i);
    await expect(chart.or(chartTable).or(emptyState).first()).toBeVisible();
  });

  test('keyboard: a nav link can be focused and activated with the keyboard', async ({ page }) => {
    await page.goto('/admin');

    // Tabbing from the top of the document lands focus on an operable control.
    await page.keyboard.press('Tab');
    const activeTag = await page.evaluate(() => document.activeElement?.tagName ?? '');
    expect(['A', 'BUTTON', 'INPUT', 'SELECT']).toContain(activeTag);

    // Focus a specific nav link and activate it with Enter (no mouse).
    const usersLink = page.getByRole('link', { name: /^Users$/ });
    await usersLink.focus();
    await expect(usersLink).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/admin\/users/);
  });

  test('mobile 320px: no horizontal scroll on Overview or Users', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 720 });

    for (const path of ['/admin', '/admin/users']) {
      await page.goto(path);
      // Wait for content to hydrate before measuring layout width.
      await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 15_000 });
      const overflow = await page.evaluate(() => document.body.scrollWidth - window.innerWidth);
      // Allow a 2px tolerance for sub-pixel rounding; the layout must not
      // introduce a horizontal scrollbar at the smallest supported width.
      expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(2);
    }
  });

  test('overview surfaces an error state with a working retry control', async ({ page }) => {
    // Force the KPIs endpoint to fail so the page must render its error state.
    await page.route('**/admin/kpis**', (route) => route.abort());
    await page.goto('/admin');

    // Explicit error state (role="alert") + a retry affordance - never blank.
    const errorRegion = page.getByRole('alert').filter({ hasText: /Couldn't load KPIs/i });
    await expect(errorRegion).toBeVisible({ timeout: 15_000 });
    const retry = page.getByRole('button', { name: /Try again/i }).first();
    await expect(retry).toBeVisible();

    // Recover: stop intercepting, retry, and confirm the KPI cards return.
    await page.unroute('**/admin/kpis**');
    await retry.click();
    await expect(page.getByText(/Total users/i)).toBeVisible({ timeout: 15_000 });
  });

  test('capability-gated manage controls render for the manage admin (read-only gap noted)', async ({
    page,
  }) => {
    // See the describe-block GAP note: a true non-admin / read-only-admin block
    // cannot be exercised in SINGLE_USER_MODE (one manage-capable user). We
    // assert the manage-only Maintenance panel IS shown to this admin; the
    // server-side authz boundary is covered by backend task 18.1.
    await page.goto('/admin/health');
    await page.getByRole('tab', { name: /Configuration/i }).click();
    await expect(page.getByRole('heading', { name: /^Maintenance$/i })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole('button', { name: /Run rollup/i })).toBeVisible();
  });
});
