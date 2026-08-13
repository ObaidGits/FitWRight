/**
 * Autofill: write the user's profile into an application form.
 *
 * Two deliberate boundaries:
 *  1. **Never submits.** This fills fields and stops. An unreviewed application
 *     going to a real employer is not a tradeoff worth making, and it is also
 *     what keeps the extension inside Chrome Web Store policy.
 *  2. **Never overwrites.** A field the user already typed into is left alone,
 *     so re-running autofill after manual edits is safe.
 */
import {
  fileFromDataUrl,
  setFileInput,
  setRadioGroup,
  setValue,
  labelFor,
} from '@/lib/dom';
import type { Fillable } from '@/lib/dom';
import {
  classify,
  collectFields,
  findOpenQuestions,
  findResumeInput,
  valueFor,
} from '@/lib/fields';
import { sendToWorker } from '@/lib/messages';
import { getSettings } from '@/lib/storage';
import type { AutofillProfile } from '@/lib/types';

/** One field the form asked for, as the registry needs to hear about it. */
export interface SeenField {
  label: string;
  field_type: string;
  options: string[];
  filled: boolean;
  matched_key: string | null;
}

export interface AutofillReport {
  filled: number;
  skipped: number;
  /** Open-ended questions found but left for the user / AI drafting. */
  questions: string[];
  resumeAttached: boolean;
  /**
   * True when the attached resume was the one tailored for this company+role
   * rather than the master. Reported so the panel can tell the user which of the
   * two actually happened instead of implying the better one.
   */
  resumeTailored: boolean;
  /**
   * Set when the form had fields but none could be filled - the signature of a
   * stale adapter or a flow never run against before, as opposed to a form that
   * was simply already complete. Holds how many fields were seen.
   */
  unrecognised?: number;
  /**
   * Every field encountered, so the learning loop can record what this form
   * asked and queue whatever we could not answer. Labels, types and options
   * only - never the values, which stay on the page unless the user explicitly
   * saves them.
   */
  seen: SeenField[];
}

/** The visible question for a field, as a person reads it. Re-exported so the
 *  content script can describe fields without importing from two modules. */
export { labelFor } from '@/lib/dom';

/** The choices a select or radio group offers, so Settings can render them. */
export function optionsFor(el: Fillable, root: ParentNode = document): string[] {
  if (el.tagName === 'SELECT') {
    return Array.from((el as HTMLSelectElement).options)
      .map((o) => o.textContent?.trim() ?? '')
      .filter((text) => text && !/^(select|choose|--)/i.test(text));
  }
  const name = el.getAttribute('name');
  const type = (el as HTMLInputElement).type;
  if (name && (type === 'radio' || type === 'checkbox')) {
    return Array.from(
      root.querySelectorAll<HTMLInputElement>(
        `input[name="${CSS.escape(name)}"]`,
      ),
    )
      .map((input) => labelFor(input))
      .filter(Boolean);
  }
  return [];
}

/** The input kind, in the vocabulary the registry stores. */
export function typeFor(el: Fillable): string {
  if (el.tagName === 'TEXTAREA') return 'textarea';
  if (el.tagName === 'SELECT') return 'select';
  const type = (el as HTMLInputElement).type?.toLowerCase();
  if (type === 'radio' || type === 'checkbox' || type === 'date' || type === 'number') return type;
  return 'text';
}

/** True when a field already holds a user-entered value. */
function hasValue(el: Fillable): boolean {
  return Boolean((el as HTMLInputElement).value?.trim());
}

/**
 * Fill every field we can confidently classify inside `root`.
 *
 * `job` names the posting this form belongs to. It is what lets the resume
 * tailored for this role be attached instead of the master one, so callers
 * should pass it whenever the page told us which job it is.
 */
export async function autofill(
  root: ParentNode = document,
  job?: { company?: string; title?: string },
): Promise<AutofillReport> {
  const report: AutofillReport = {
    filled: 0,
    skipped: 0,
    questions: [],
    resumeAttached: false,
    resumeTailored: false,
    seen: [],
  };

  const profileReply = await sendToWorker({ type: 'get-profile' });
  if (!profileReply.ok) throw new Error(profileReply.error);
  const profile: AutofillProfile = profileReply.data;
  const { preferences } = await getSettings();

  /** Record what a field asked and whether we answered it. */
  function note(el: Fillable, key: string | null, filled: boolean): void {
    const label = labelFor(el);
    if (!label) return; // nothing a person could recognise it by
    // A password field is never reported at all, not even its label.
    if ((el as HTMLInputElement).type === 'password') return;
    report.seen.push({
      label,
      field_type: typeFor(el),
      options: optionsFor(el, root),
      filled,
      matched_key: key,
    });
  }

  // --- Text / select fields -------------------------------------------------
  for (const el of collectFields(root)) {
    if (hasValue(el)) {
      report.skipped += 1;
      // Already answered - by us on an earlier step, or by the user. Either way
      // this question is not outstanding, so it is reported as filled.
      note(el, classify(el), true);
      continue;
    }
    const key = classify(el);
    if (!key) {
      // Unrecognised: the single most useful thing to learn about, since this is
      // exactly what the user will have to answer by hand.
      note(el, null, false);
      continue;
    }

    const value = valueFor(key, profile, preferences);
    if (!value) {
      // Classified but we have nothing to say - e.g. the user left EEO answers
      // blank. Leaving it empty is correct; guessing is not.
      report.skipped += 1;
      note(el, key, false);
      continue;
    }
    const ok = setValue(el, value);
    if (ok) report.filled += 1;
    else report.skipped += 1;
    note(el, key, ok);
  }

  // --- Radio / checkbox groups ---------------------------------------------
  // Handled separately: they are addressed by `name`, not per element, and the
  // value has to match an option's label rather than being typed.
  const groups = new Map<string, HTMLInputElement>();
  for (const input of root.querySelectorAll<HTMLInputElement>(
    'input[type="radio"], input[type="checkbox"]',
  )) {
    const name = input.getAttribute('name');
    if (name && !groups.has(name)) groups.set(name, input);
  }
  for (const [name, sample] of groups) {
    const key = classify(sample);
    if (!key) {
      note(sample, null, false);
      continue;
    }
    const value = valueFor(key, profile, preferences);
    if (!value) {
      note(sample, key, false);
      continue;
    }
    const ok = setRadioGroup(name, value, root);
    if (ok) report.filled += 1;
    note(sample, key, ok);
  }

  // --- Resume upload -------------------------------------------------------
  const fileInput = findResumeInput(root);
  if (fileInput && !fileInput.files?.length) {
    const pdfReply = await sendToWorker({
      type: 'get-resume-pdf',
      company: job?.company,
      title: job?.title,
    });
    if (pdfReply.ok && pdfReply.data) {
      const file = fileFromDataUrl(pdfReply.data.dataUrl, pdfReply.data.filename);
      if (file && setFileInput(fileInput, file)) {
        report.resumeAttached = true;
        report.resumeTailored = pdfReply.data.tailored;
        report.filled += 1;
      }
    }
  }

  // --- Open-ended questions ------------------------------------------------
  // Reported, not answered: drafting costs an LLM call each, so the user opts in
  // per question from the popup rather than firing N calls on every autofill.
  report.questions = findOpenQuestions(root).map((q) => q.question);

  // A form that is plainly a form, where nothing was recognised, is a different
  // event from a form that was already complete - and the difference matters,
  // because the first one means an adapter has gone stale (a site redesign, or a
  // flow we have never run against). Reporting "0 fields filled" for both is how
  // a broken adapter stays broken for weeks: the user reads it as their own
  // mistake and stops trying.
  //
  // Nothing is lost when this fires: every label was still sent to the learning
  // loop, so the questions land in Answers and the next attempt can fill them.
  if (report.filled === 0 && report.seen.length > 0) {
    report.unrecognised = report.seen.length;
  }

  return report;
}

/**
 * Draft and fill answers for the page's open-ended questions.
 * Separate from `autofill` because it is the expensive, opt-in half.
 */
export async function draftOpenQuestions(
  job: { title: string; company: string; description: string },
  root: ParentNode = document,
): Promise<{ drafted: number; failed: number }> {
  const questions = findOpenQuestions(root);
  let drafted = 0;
  let failed = 0;

  for (const { el, question } of questions) {
    const reply = await sendToWorker({
      type: 'draft',
      question,
      description: job.description,
      company: job.company,
      title: job.title,
    });
    if (!reply.ok || !reply.data.answer) {
      failed += 1;
      continue;
    }
    if (setValue(el, reply.data.answer)) {
      markAsDraft(el);
      drafted += 1;
    } else failed += 1;
  }
  return { drafted, failed };
}

/**
 * Mark a field as holding an AI draft, until the user touches it.
 *
 * A toast saying "3 answers drafted - review before submitting" scrolls away in
 * four seconds; the text left in the box looks exactly like something the user
 * wrote. Employers are being sent these words in the user's name, so the field
 * itself has to carry the warning, not a notification about the field.
 *
 * The mark clears on first input: once they have edited it, it is theirs.
 */
function markAsDraft(el: Fillable): void {
  const styled = el as HTMLElement;
  styled.dataset.fitwrightDraft = 'true';
  styled.style.outline = '2px dashed rgba(217, 119, 6, .9)';
  styled.style.outlineOffset = '1px';
  styled.style.backgroundColor = 'rgba(217, 119, 6, .06)';
  if (!styled.title) styled.title = 'Drafted by FitWright - review before submitting';

  const clear = () => {
    delete styled.dataset.fitwrightDraft;
    styled.style.outline = '';
    styled.style.outlineOffset = '';
    styled.style.backgroundColor = '';
    if (styled.title === 'Drafted by FitWright - review before submitting') styled.title = '';
    styled.removeEventListener('input', clear);
  };
  styled.addEventListener('input', clear, { once: true });
}

/** Questions on the page, for the popup to list before drafting. */
export function listOpenQuestions(root: ParentNode = document): string[] {
  return findOpenQuestions(root).map(({ el, question }) => question || labelFor(el));
}
