import { test, expect } from '@playwright/test';

/**
 * Core-path E2E (Task 17.4 / Req 24.5).
 *
 * The navigation + real-data smoke path runs against the live stack and is
 * quota-independent. The AI-native core (import → tailor → cover letter →
 * export) is authored below but gated behind RUN_AI_E2E=1 so it only fires when
 * a funded, non-rate-limited LLM key is configured — this keeps the suite green
 * without spending provider quota on every run.
 */

test.describe('FitWright — navigation & real-data smoke', () => {
  test('landing page renders the hero, story sections and primary CTA', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/FitWright/i);
    // Hero headline + primary CTA.
    await expect(page.getByRole('heading', { name: /Your resume should be too/i })).toBeVisible();
    await expect(page.getByRole('link', { name: /Start tailoring/i }).first()).toBeVisible();
    // A couple of story sections render.
    await expect(page.getByRole('heading', { name: /Bring your own API key/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Questions, answered/i })).toBeVisible();
  });

  test('app shell: Home → Resumes → Applications navigation works', async ({ page }) => {
    await page.goto('/home');
    // Primary launchpad action is always present.
    await expect(page.getByRole('link', { name: /Tailor to a job/i }).first()).toBeVisible();

    await page.goto('/resumes');
    await expect(page.getByRole('heading', { name: /Resumes/i })).toBeVisible();

    await page.goto('/applications');
    await expect(page.getByRole('heading', { name: /Applications/i })).toBeVisible();
  });

  test('resumes library lists the master resume and opens the editor with a live preview', async ({
    page,
  }) => {
    await page.goto('/resumes');
    // Open the first resume's editor via its actions or title link.
    const firstOpen = page.getByRole('link', { name: /open|edit/i }).first();
    if (await firstOpen.count()) {
      await firstOpen.click();
    } else {
      // Fall back to clicking the first resume card title.
      await page.locator('a[href^="/resumes/"]').first().click();
    }
    await expect(page).toHaveURL(/\/resumes\/.+/);
    // The editor exposes a Save action and an always-visible Live preview.
    await expect(page.getByText(/Live preview/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /^Save$/ })).toBeVisible();
    // Appearance + Export actions are present.
    await expect(page.getByRole('button', { name: /Appearance/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Export PDF/i })).toBeVisible();
  });

  test('tailor surface loads with source resume + JD input', async ({ page }) => {
    await page.goto('/tailor');
    await expect(page.getByRole('heading', { name: /Tailor to a job/i })).toBeVisible();
    await expect(page.getByLabel(/Job description/i)).toBeVisible();
    // Generate is disabled until a long-enough JD is pasted.
    await expect(page.getByRole('button', { name: /^Generate$/ })).toBeVisible();
  });
});

const AI = process.env.RUN_AI_E2E === '1';

test.describe('FitWright — AI-native core (requires quota)', () => {
  test.skip(!AI, 'Set RUN_AI_E2E=1 with a funded LLM key to run the AI path.');

  test('tailor → review → accept creates an application', async ({ page }) => {
    await page.goto('/tailor');
    const jd =
      'Senior Backend Engineer. Python, FastAPI, PostgreSQL, Docker, AWS. Design scalable REST APIs, ' +
      'optimize database performance, mentor engineers. 5+ years, CI/CD, microservices.';
    await page.getByLabel(/Job description/i).fill(jd);
    await page.getByRole('button', { name: /^Generate$/ }).click();
    // Review surface: a match score ring + change summary appear when done.
    await expect(page.getByText(/Match score/i)).toBeVisible({ timeout: 240_000 });
    await page.getByRole('button', { name: /Accept & save/i }).click();
    await expect(page).toHaveURL(/\/applications/, { timeout: 60_000 });
  });

  test('application workspace generates and exports a cover letter', async ({ page }) => {
    await page.goto('/applications');
    await page.locator('a[href^="/applications/"]').first().click();
    await page.getByRole('tab', { name: /Cover Letter/i }).click();
    await page.getByRole('button', { name: /Generate cover letter/i }).click();
    await expect(page.getByRole('button', { name: /Export PDF/i })).toBeVisible({
      timeout: 180_000,
    });
  });
});
