/**
 * Field classification: given a form input, decide what to put in it.
 *
 * This is heuristic by necessity - there is no standard for application form
 * field names, and every ATS invents its own (`cand_first_nm`,
 * `data-automation-id="legalNameSection_firstName"`, `question_2847`). So we
 * match against every signal the field exposes (label, name, id, placeholder,
 * autocomplete, testids) and take the first rule that hits.
 *
 * Rule order is significant: the list is scanned top-down, so narrow patterns
 * must precede broad ones. "first name" has to beat plain "name", and
 * "linkedin url" has to beat a generic "url".
 */
import { fieldSignals, isFillable, labelFor } from './dom';
import type { Fillable } from './dom';
import type { AutofillProfile, LocalPreferences } from './types';

/** Which value a matched field should receive. */
export type FieldKey =
  | keyof Pick<
      AutofillProfile,
      | 'full_name'
      | 'first_name'
      | 'last_name'
      | 'email'
      | 'phone'
      | 'location'
      | 'linkedin'
      | 'github'
      | 'website'
      | 'current_title'
      | 'current_company'
      // Structured address: ATS forms ask for these as separate inputs.
      | 'address_line1'
      | 'address_line2'
      | 'city'
      | 'state'
      | 'postal_code'
      | 'country'
      // Highest education.
      | 'highest_degree'
      | 'highest_institution'
      | 'education_years'
      // Eligibility, served from the user's Profile in FitWright.
      | 'availability'
      | 'remote_preference'
      | 'visa_status'
    >
  | 'years_experience'
  | 'willing_to_relocate'
  | keyof Omit<LocalPreferences, 'custom'>;

interface Rule {
  key: FieldKey;
  /** Any pattern matching the field's signals selects this rule. */
  match: RegExp;
  /** Patterns that veto the match - guards against near-miss labels. */
  reject?: RegExp;
}

/**
 * Ordered rules. Narrow first.
 *
 * The `reject` guards exist because of real collisions: a "Company" field on an
 * application is the candidate's current employer, but "Why do you want to work
 * at our company" is a free-text question, not a company name.
 */
const RULES: Rule[] = [
  // --- Name: split fields before the combined one ---
  { key: 'first_name', match: /first[\s_-]*name|given[\s_-]*name|\bfname\b|legalname.*first/ },
  {
    key: 'last_name',
    match: /last[\s_-]*name|family[\s_-]*name|surname|\blname\b|legalname.*last/,
  },
  {
    key: 'full_name',
    match: /\bname\b|full[\s_-]*name/,
    reject: /first|last|given|family|sur|user|company|employer|school|university|file|middle|preferred|referr/,
  },

  // --- Contact ---
  { key: 'email', match: /e-?mail/ },
  { key: 'phone', match: /phone|mobile|tel(ephone)?\b|contact[\s_-]*number/ },
  {
    key: 'location',
    // Deliberately narrow. This is the one-line "where are you based" field, so
    // it must NOT swallow "City", "Address line 1", "State" or "Country" - those
    // have their own rules further down, and RULES is first-match-wins. Before
    // this was narrowed, a form asking for city/state/zip separately had all
    // three filled with "Pune, India".
    match: /location|where.*(based|located)|current[\s_-]*(city|location)|based[\s_-]*in/,
    reject: /email|url|relocat|willing|zip|postal|country|province/,
  },

  // --- Links: each before the generic website rule ---
  { key: 'linkedin', match: /linked[\s_-]*in/ },
  { key: 'github', match: /git[\s_-]*hub/ },
  {
    key: 'website',
    match: /website|portfolio|personal[\s_-]*site|\bblog\b|\burl\b/,
    reject: /linked|git|company|job|posting/,
  },

  // --- Current role ---
  {
    key: 'current_title',
    match: /current[\s_-]*(job[\s_-]*)?title|job[\s_-]*title|your[\s_-]*title|position|role/,
    reject: /desired|preferred|apply|applied|why|describe/,
  },
  {
    key: 'current_company',
    match: /current[\s_-]*(company|employer)|company[\s_-]*name|\bemployer\b/,
    reject: /why|our[\s_-]*company|describe|interest/,
  },

  // --- Experience ---
  {
    key: 'years_experience',
    match: /years?[\s_-]*(of[\s_-]*)?experience|total[\s_-]*experience|\byoe\b|exp.*years/,
  },

  // --- Work authorization / sponsorship: sponsorship first, it is narrower ---
  {
    key: 'requiresSponsorship',
    match: /sponsor(ship)?|require.*visa|need.*visa|h-?1b/,
  },
  {
    key: 'workAuthorization',
    match: /work[\s_-]*(authoriz|permit|elig)|legally.*(work|authoriz)|right[\s_-]*to[\s_-]*work|citizenship/,
  },

  // --- Logistics ---
  {
    key: 'noticePeriod',
    match: /notice[\s_-]*period|available.*start|start[\s_-]*date|when.*(start|join)|joining/,
  },
  {
    key: 'salaryExpectation',
    match: /salary|compensation|\bctc\b|expected[\s_-]*pay|desired[\s_-]*(salary|compensation)|pay[\s_-]*expect/,
  },

  // --- EEO / demographics (always optional; filled only if the user set them) ---
  { key: 'gender', match: /gender|\bsex\b/, reject: /orientation/ },
  { key: 'ethnicity', match: /ethnic|race|hispanic|latino/ },
  { key: 'veteranStatus', match: /veteran|military/ },
  { key: 'disabilityStatus', match: /disab/ },

  // --- Structured address --------------------------------------------------- #
  // Ordered before the generic `location` rule would be reached for these, and
  // each rejects the others' wording so "address line 2" cannot match line 1.
  {
    key: 'address_line2',
    match: /address[\s_-]*(line[\s_-]*)?2|apt|apartment|unit|suite|line[\s_-]*2/,
  },
  {
    key: 'address_line1',
    match: /street|address[\s_-]*(line[\s_-]*)?1|address$|^address|mailing[\s_-]*address/,
    reject: /line[\s_-]*2|apt|apartment|unit|suite|email|ip[\s_-]*address|city|state|zip|postal|country/,
  },
  { key: 'city', match: /\bcity\b|\btown\b/, reject: /citizen/ },
  {
    key: 'state',
    match: /\bstate\b|province|\bregion\b|county/,
    reject: /united[\s_-]*states|statement|employment[\s_-]*state/,
  },
  { key: 'postal_code', match: /zip|postal|post[\s_-]*code|pincode|pin[\s_-]*code/ },
  { key: 'country', match: /country|nation(ality)?/, reject: /county/ },

  // --- Education ------------------------------------------------------------ #
  {
    key: 'highest_degree',
    match: /degree|qualification|highest[\s_-]*(level[\s_-]*of[\s_-]*)?education|education[\s_-]*level/,
  },
  {
    key: 'highest_institution',
    match: /university|college|school[\s_-]*name|institut|alma[\s_-]*mater/,
    reject: /high[\s_-]*school[\s_-]*diploma/,
  },
  { key: 'education_years', match: /graduation[\s_-]*(year|date)|year[\s_-]*of[\s_-]*graduation/ },

  // --- Availability / preferences ------------------------------------------- #
  {
    key: 'availability',
    match: /available[\s_-]*(from|start)?|start[\s_-]*date|earliest[\s_-]*start|when.*start/,
    reject: /notice/,
  },
  { key: 'willing_to_relocate', match: /relocat/ },
  {
    key: 'remote_preference',
    match: /remote[\s_-]*(work|preference)?|work[\s_-]*(mode|arrangement|setting)|onsite|hybrid/,
    reject: /remote[\s_-]*id/,
  },
];

/** Classify one field, or null when nothing matches confidently. */
export function classify(el: Fillable): FieldKey | null {
  const signals = fieldSignals(el);
  if (!signals.trim()) return null;

  // `type` is a strong hint that overrides label guessing.
  const type = (el as HTMLInputElement).type?.toLowerCase();
  if (type === 'email') return 'email';
  if (type === 'tel') return 'phone';

  for (const rule of RULES) {
    if (!rule.match.test(signals)) continue;
    if (rule.reject?.test(signals)) continue;
    return rule.key;
  }
  return null;
}

/** Resolve the string to type into a classified field. */
/**
 * The value a matched field should receive.
 *
 * Precedence is the point of this function: FitWright's Profile is the source of
 * truth, and the extension's locally stored answers are only a fallback for what
 * the server does not (yet) hold. That ordering is what lets the user edit one
 * place - Settings in FitWright - and have every form follow, instead of
 * discovering that a stale answer cached in this browser silently won.
 *
 * EEO/demographic answers are the deliberate exception: they are never sourced
 * from the server profile and stay local-only, because they must never be
 * inferred or shared, and "prefer not to say" is a valid final answer.
 */
export function valueFor(
  key: FieldKey,
  profile: AutofillProfile,
  preferences: LocalPreferences,
): string {
  switch (key) {
    case 'years_experience':
      return profile.years_experience == null ? '' : String(profile.years_experience);

    case 'willing_to_relocate':
      // Tri-state on the wire: null means unanswered, so send nothing.
      if (profile.willing_to_relocate == null) return '';
      return profile.willing_to_relocate ? 'Yes' : 'No';

    // Eligibility answers now live in the Profile; fall back to whatever the
    // older extension build stored locally so upgrades lose nothing.
    case 'workAuthorization':
      return profile.work_authorization || preferences.workAuthorization || '';
    case 'noticePeriod':
      return profile.notice_period || preferences.noticePeriod || '';
    case 'salaryExpectation':
      return profile.salary_expectation || preferences.salaryExpectation || '';

    // Sponsorship is asked both ways round ("do you require sponsorship?" vs
    // "are you authorized to work?"), so it stays a separate stored answer.
    case 'requiresSponsorship':
      return preferences.requiresSponsorship || '';

    // Local-only by design. See the note above.
    case 'gender':
    case 'ethnicity':
    case 'veteranStatus':
    case 'disabilityStatus':
      return preferences[key] ?? '';

    default:
      return profile[key] ?? '';
  }
}

/** Every writable field in the page, in document order. */
export function collectFields(root: ParentNode = document): Fillable[] {
  const nodes = root.querySelectorAll<Fillable>('input, textarea, select');
  return Array.from(nodes).filter((el) => {
    const type = (el as HTMLInputElement).type?.toLowerCase();
    if (type === 'file') return false; // handled separately by the resume upload
    if (type === 'radio' || type === 'checkbox') return false; // handled as groups
    return isFillable(el);
  });
}

/** File inputs that look like a resume/CV upload slot. */
export function findResumeInput(root: ParentNode = document): HTMLInputElement | null {
  const inputs = Array.from(root.querySelectorAll<HTMLInputElement>('input[type="file"]'));
  if (!inputs.length) return null;

  const resumeLike = /resume|cv\b|curriculum/i;
  const match = inputs.find((el) => resumeLike.test(fieldSignals(el)));
  // A form with exactly one file input is a resume slot often enough to use it,
  // but with several we only take an explicitly labelled one - guessing risks
  // putting the resume in a cover-letter or portfolio slot.
  return match ?? (inputs.length === 1 ? inputs[0] : null);
}

/** Long-form questions the LLM should draft, rather than autofill. */
const OPEN_ENDED = /why|describe|tell us|explain|what (makes|interests|excites)|cover letter|motivat|challeng|proud|accomplish|about yourself|in your own words/i;

/**
 * Free-text questions worth drafting an answer for.
 *
 * Gated on textarea-or-long-input plus an interrogative label so short
 * identity fields never get routed to the LLM.
 */
export function findOpenQuestions(root: ParentNode = document): Array<{
  el: Fillable;
  question: string;
}> {
  const found: Array<{ el: Fillable; question: string }> = [];
  const fields = collectFields(root);

  for (const el of fields) {
    if (classify(el)) continue; // already a known identity field
    const isLongForm =
      el instanceof HTMLTextAreaElement ||
      Number(el.getAttribute('maxlength') ?? 0) > 200;
    if (!isLongForm) continue;

    const signals = fieldSignals(el);
    if (!OPEN_ENDED.test(signals)) continue;
    if ((el as HTMLInputElement).value?.trim()) continue; // user already answered

    const question = labelFor(el) || el.getAttribute('placeholder') || 'Application question';
    found.push({ el, question });
  }
  return found;
}
