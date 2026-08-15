/**
 * autofill() decision recording (auto-apply-brain Phase 0).
 *
 * Every fill attempt must produce a Decision with a real source and a read-back
 * result, because grading (app.brain_grading on the backend) and the eventual
 * confidence-gated auto-submit are computed from exactly these records - never
 * estimated after the fact. A decision silently missing here is a field that
 * cannot be graded.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AutofillProfile } from '@/lib/types';

function minimalProfile(overrides: Partial<AutofillProfile> = {}): AutofillProfile {
  return {
    full_name: '',
    first_name: '',
    last_name: '',
    email: '',
    phone: '',
    location: '',
    linkedin: '',
    github: '',
    website: '',
    current_title: '',
    current_company: '',
    years_experience: null,
    address_line1: '',
    address_line2: '',
    city: '',
    state: '',
    postal_code: '',
    country: '',
    work_authorization: '',
    visa_status: '',
    notice_period: '',
    salary_expectation: '',
    willing_to_relocate: null,
    availability: '',
    remote_preference: '',
    derived_eligibility_fields: [],
    highest_degree: '',
    highest_institution: '',
    education_years: '',
    resume_id: null,
    resume_filename: '',
    resume_pdf_path: null,
    preferences: {},
    ...overrides,
  };
}

describe('autofill decision recording', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    // jsdom never lays out elements, so getBoundingClientRect is always
    // zero-sized - and isFillable/isVisible in lib/dom.ts requires a non-zero
    // rect. Every field in these fixtures must appear "visible" to be reached.
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 100,
      height: 20,
      top: 0,
      left: 0,
      right: 100,
      bottom: 20,
      x: 0,
      y: 0,
      toJSON() {
        return this;
      },
    });
    vi.stubGlobal('chrome', {
      runtime: {
        sendMessage: vi.fn(async (message: { type: string }) => {
          if (message.type === 'get-profile') {
            return { ok: true, data: minimalProfile({ email: 'jane@example.com' }) };
          }
          if (message.type === 'get-resume-pdf') {
            return { ok: true, data: null };
          }
          return { ok: false, error: `unhandled message: ${message.type}` };
        }),
      },
      storage: {
        sync: { get: vi.fn(async () => ({})), set: vi.fn(async () => undefined) },
        local: { get: vi.fn(async () => ({})), set: vi.fn(async () => undefined) },
      },
    });
  });

  it('records a filled, verified decision for a field the rules matched', async () => {
    document.body.innerHTML = `
      <form>
        <label for="email">Email</label>
        <input id="email" name="email" type="email" />
      </form>
    `;
    const { autofill } = await import('@/content/autofill');
    const report = await autofill(document);

    const decision = report.decisions.find((d) => d.label === 'Email');
    expect(decision).toBeDefined();
    expect(decision?.value_source).toBe('exact_rule');
    expect(decision?.filled).toBe(true);
    expect(decision?.readback_ok).toBe(true);
    expect(decision?.resolved_target).toBe('email');
  });

  it('records a field already holding a value as user_answer with no read-back claim', async () => {
    document.body.innerHTML = `
      <form>
        <label for="email">Email</label>
        <input id="email" name="email" type="email" value="typed-by-user@example.com" />
      </form>
    `;
    const { autofill } = await import('@/content/autofill');
    const report = await autofill(document);

    const decision = report.decisions.find((d) => d.label === 'Email');
    expect(decision?.value_source).toBe('user_answer');
    expect(decision?.filled).toBe(true);
    // Nothing was attempted against the user's own typing, so there is nothing
    // to have verified - null, not true.
    expect(decision?.readback_ok).toBeNull();
  });

  it('produces no decision for a field the rules could not classify', async () => {
    document.body.innerHTML = `
      <form>
        <label for="mystery">Favourite fruit</label>
        <input id="mystery" name="mystery" />
      </form>
    `;
    const { autofill } = await import('@/content/autofill');
    const report = await autofill(document);

    // Unclassified fields are reported in `seen` (for the learning loop) but
    // never in `decisions` - there is no source to grade, because nothing was
    // decided.
    expect(report.seen.some((f) => f.label === 'Favourite fruit')).toBe(true);
    expect(report.decisions.some((d) => d.label === 'Favourite fruit')).toBe(false);
  });

  it('tags a field the profile marked as country-derived as derived_rule, not exact_rule', async () => {
    vi.stubGlobal('chrome', {
      runtime: {
        sendMessage: vi.fn(async (message: { type: string }) => {
          if (message.type === 'get-profile') {
            return {
              ok: true,
              data: minimalProfile({
                work_authorization: 'No - authorized to work',
                // FieldKey 'workAuthorization' maps to profile field
                // 'work_authorization' via PROFILE_FIELD_FOR_KEY - this is
                // exactly the non-trivial case that mapping exists for.
                derived_eligibility_fields: ['work_authorization'],
              }),
            };
          }
          if (message.type === 'get-resume-pdf') return { ok: true, data: null };
          return { ok: false, error: `unhandled message: ${message.type}` };
        }),
      },
      storage: {
        sync: { get: vi.fn(async () => ({})), set: vi.fn(async () => undefined) },
        local: { get: vi.fn(async () => ({})), set: vi.fn(async () => undefined) },
      },
    });
    document.body.innerHTML = `
      <form>
        <label for="auth">Are you legally authorized to work in this country?</label>
        <input id="auth" name="auth" />
      </form>
    `;
    const { autofill } = await import('@/content/autofill');
    const report = await autofill(document);

    const decision = report.decisions.find(
      (d) => d.label === 'Are you legally authorized to work in this country?',
    );
    expect(decision?.value_source).toBe('derived_rule');
    expect(decision?.filled).toBe(true);
  });
});
