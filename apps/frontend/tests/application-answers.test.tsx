/**
 * Application answers: grouping rules and the Settings section.
 *
 * The grouping tests matter more than they look. `groupForField` decides whether
 * this page stays usable after a month of applying, and its rules are ordered -
 * first match wins - so a broad pattern placed too early silently swallows a
 * specific one ("postal code" landing in Personal via "code").
 */
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import { groupForField, type ApplicationField } from '@/lib/api/application-fields';

vi.mock('@/components/atelier/toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

function field(overrides: Partial<ApplicationField> = {}): ApplicationField {
  return {
    id: 'f1',
    label: 'A question',
    label_normalized: 'a question',
    synonyms: [],
    field_type: 'text',
    options: [],
    value: null,
    profile_path: null,
    from_profile: false,
    scope: 'global',
    company: null,
    status: 'needs_answer',
    source: 'learned',
    is_knockout: false,
    times_seen: 1,
    last_seen_at: null,
    last_seen_url: null,
    last_seen_ats: null,
    ...overrides,
  };
}

describe('groupForField', () => {
  const cases: Array<[string, string]> = [
    // Address must win over Personal, which also matches loosely.
    ['street address', 'Address'],
    ['city', 'Address'],
    ['state province', 'Address'],
    ['postal code', 'Address'],
    ['zip code', 'Address'],
    ['country', 'Address'],
    // Eligibility.
    ['do you require visa sponsorship', 'Eligibility & Work Authorization'],
    ['work authorization', 'Eligibility & Work Authorization'],
    ['are you legally authorized to work', 'Eligibility & Work Authorization'],
    // Compensation and availability.
    ['expected salary', 'Compensation & Availability'],
    ['notice period', 'Compensation & Availability'],
    ['are you willing to relocate', 'Compensation & Availability'],
    ['earliest start date', 'Compensation & Availability'],
    // Education.
    ['highest degree', 'Education'],
    ['university', 'Education'],
    ['graduation year', 'Education'],
    // Work history.
    ['years of experience', 'Work History'],
    ['current employer', 'Work History'],
    ['job title', 'Work History'],
    // Personal.
    ['first name', 'Personal & Contact'],
    ['email', 'Personal & Contact'],
    ['linkedin', 'Personal & Contact'],
    // Anything unrecognised stays visible rather than being mis-filed.
    ['how did you hear about us', 'Custom'],
    ['what is your favourite editor', 'Custom'],
  ];

  it.each(cases)('%s -> %s', (label, expected) => {
    expect(groupForField({ label_normalized: label })).toBe(expected);
  });

  it('does not put postal code in Personal despite loose name matching', () => {
    expect(groupForField({ label_normalized: 'postal code' })).not.toBe('Personal & Contact');
  });
});

// The component is exercised through a mocked API layer so these stay unit tests.
const listMock = vi.fn();
vi.mock('@/lib/api/application-fields', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/application-fields')>();
  return {
    ...actual,
    listApplicationFields: (...args: unknown[]) => listMock(...args),
    updateApplicationField: vi.fn(),
    deleteApplicationField: vi.fn(),
    mergeApplicationFields: vi.fn(),
  };
});

async function renderSection() {
  const { ApplicationAnswers } = await import('@/components/answers/application-answers');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ApplicationAnswers />
    </QueryClientProvider>,
  );
}

describe('ApplicationAnswers', () => {
  it('leads with the questions that need an answer', async () => {
    listMock.mockResolvedValue([
      field({ id: '1', label: 'Do you require sponsorship?', status: 'needs_answer' }),
      field({ id: '2', label: 'Email', status: 'answered', value: 'a@b.c' }),
    ]);
    await renderSection();

    expect(await screen.findByText('Needs your answer (1)')).toBeInTheDocument();
    expect(screen.getByText('Do you require sponsorship?')).toBeInTheDocument();
  });

  it('tells a new user why the page is empty instead of showing a bare list', async () => {
    listMock.mockResolvedValue([]);
    await renderSection();
    expect(await screen.findByText('Nothing asked yet')).toBeInTheDocument();
  });

  it('says so when everything has an answer', async () => {
    listMock.mockResolvedValue([field({ status: 'answered', value: 'x' })]);
    await renderSection();
    expect(await screen.findByText(/Every question so far has an answer/)).toBeInTheDocument();
  });

  it('marks a Profile-backed answer read-only, pointing the user at one place to edit', async () => {
    listMock.mockResolvedValue([
      field({
        label: 'Work Authorization',
        // Grouping keys off the normalized label; Eligibility is the one group
        // open by default, so this renders without expanding anything.
        label_normalized: 'work authorization',
        status: 'answered',
        from_profile: true,
        profile_path: 'identity.workAuthorization',
        value: 'Indian citizen',
      }),
    ]);
    await renderSection();

    expect(await screen.findByText('From your Profile')).toBeInTheDocument();
    expect(screen.getByText('Edit this on your Profile page')).toBeInTheDocument();
    // Shown as text, not an input: a disabled <select> displayed the placeholder
    // whenever the Profile value was not one of the form's options, which read as
    // "unanswered" when an answer existed.
    expect(screen.getByText('Indian citizen')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument();
  });

  it('warns when a Profile answer cannot satisfy the form’s choices', async () => {
    listMock.mockResolvedValue([
      field({
        label: 'Do you require visa sponsorship?',
        label_normalized: 'do you require visa sponsorship',
        field_type: 'select',
        options: ['Yes', 'No'],
        status: 'answered',
        from_profile: true,
        profile_path: 'identity.workAuthorization',
        value: 'Indian citizen',
      }),
    ]);
    await renderSection();

    expect(await screen.findByText(/only accepts Yes or No/)).toBeInTheDocument();
  });

  it('says when a Profile-backed field has nothing in the Profile yet', async () => {
    listMock.mockResolvedValue([
      field({
        label: 'Work Authorization',
        label_normalized: 'work authorization',
        status: 'answered',
        from_profile: true,
        profile_path: 'identity.workAuthorization',
        value: null,
      }),
    ]);
    await renderSection();
    expect(await screen.findByText('Not set in your Profile yet')).toBeInTheDocument();
  });

  it('badges a screening question so a wrong answer is less likely', async () => {
    listMock.mockResolvedValue([
      field({ label: 'Expected salary', status: 'needs_answer', is_knockout: true }),
    ]);
    await renderSection();
    expect(await screen.findByText('Screening')).toBeInTheDocument();
  });

  it("renders the form's own options rather than a free-text box", async () => {
    listMock.mockResolvedValue([
      field({
        label: 'Do you require sponsorship?',
        field_type: 'select',
        options: ['Yes', 'No'],
        status: 'needs_answer',
      }),
    ]);
    await renderSection();

    const select = await screen.findByLabelText('Do you require sponsorship?');
    expect(select.tagName).toBe('SELECT');
    expect(screen.getByRole('option', { name: 'Yes' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'No' })).toBeInTheDocument();
  });

  it('shows which company a company-scoped answer applies to', async () => {
    listMock.mockResolvedValue([
      field({
        label: 'Work Authorization',
        label_normalized: 'work authorization',
        status: 'answered',
        value: 'x',
        scope: 'company',
        company: 'Acme',
      }),
    ]);
    await renderSection();
    expect(await screen.findByText('Acme')).toBeInTheDocument();
  });

  it('keeps answered groups collapsed until asked, so the page stays scannable', async () => {
    listMock.mockResolvedValue([
      field({
        label: 'How did you hear about us?',
        label_normalized: 'how did you hear about us',
        status: 'answered',
        value: 'A friend',
      }),
    ]);
    await renderSection();

    // The group header is visible; the answer inside it is not, yet.
    const header = await screen.findByRole('button', { name: /Custom \(1\)/ });
    expect(screen.queryByText('How did you hear about us?')).not.toBeInTheDocument();

    header.click();
    expect(await screen.findByText('How did you hear about us?')).toBeInTheDocument();
  });
});
