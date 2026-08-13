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

export interface AutofillReport {
  filled: number;
  skipped: number;
  /** Open-ended questions found but left for the user / AI drafting. */
  questions: string[];
  resumeAttached: boolean;
}

/** True when a field already holds a user-entered value. */
function hasValue(el: Fillable): boolean {
  return Boolean((el as HTMLInputElement).value?.trim());
}

/**
 * Fill every field we can confidently classify inside `root`.
 */
export async function autofill(root: ParentNode = document): Promise<AutofillReport> {
  const report: AutofillReport = {
    filled: 0,
    skipped: 0,
    questions: [],
    resumeAttached: false,
  };

  const profileReply = await sendToWorker({ type: 'get-profile' });
  if (!profileReply.ok) throw new Error(profileReply.error);
  const profile: AutofillProfile = profileReply.data;
  const { preferences } = await getSettings();

  // --- Text / select fields -------------------------------------------------
  for (const el of collectFields(root)) {
    if (hasValue(el)) {
      report.skipped += 1;
      continue;
    }
    const key = classify(el);
    if (!key) continue;

    const value = valueFor(key, profile, preferences);
    if (!value) {
      // Classified but we have nothing to say - e.g. the user left EEO answers
      // blank. Leaving it empty is correct; guessing is not.
      report.skipped += 1;
      continue;
    }
    if (setValue(el, value)) report.filled += 1;
    else report.skipped += 1;
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
    if (!key) continue;
    const value = valueFor(key, profile, preferences);
    if (!value) continue;
    if (setRadioGroup(name, value, root)) report.filled += 1;
  }

  // --- Resume upload -------------------------------------------------------
  const fileInput = findResumeInput(root);
  if (fileInput && !fileInput.files?.length) {
    const pdfReply = await sendToWorker({ type: 'get-resume-pdf' });
    if (pdfReply.ok && pdfReply.data) {
      const file = fileFromDataUrl(pdfReply.data.dataUrl, pdfReply.data.filename);
      if (file && setFileInput(fileInput, file)) {
        report.resumeAttached = true;
        report.filled += 1;
      }
    }
  }

  // --- Open-ended questions ------------------------------------------------
  // Reported, not answered: drafting costs an LLM call each, so the user opts in
  // per question from the popup rather than firing N calls on every autofill.
  report.questions = findOpenQuestions(root).map((q) => q.question);

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
    if (setValue(el, reply.data.answer)) drafted += 1;
    else failed += 1;
  }
  return { drafted, failed };
}

/** Questions on the page, for the popup to list before drafting. */
export function listOpenQuestions(root: ParentNode = document): string[] {
  return findOpenQuestions(root).map(({ el, question }) => question || labelFor(el));
}
