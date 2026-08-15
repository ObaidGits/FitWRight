/**
 * Typed message bus for content script <-> service worker <-> popup.
 *
 * Every cross-context call goes through this discriminated union so a typo in a
 * message name is a compile error rather than a silent no-op at runtime - which
 * is the usual way extension messaging breaks.
 *
 * Direction convention:
 *  - `To*` names say who HANDLES the message, not who sends it.
 *  - All network calls are handled in the service worker: content scripts run on
 *    a third-party origin, so their `fetch` would be a cross-origin request
 *    subject to that page's CSP. The worker's origin owns the host permissions.
 */
import type {
  CaptureResponse,
  CapturedJob,
  DraftResult,
  MatchResult,
  PageContext,
  ScrapeResponse,
} from './types';

// --------------------------------------------------------------------------- //
// Messages handled by the service worker (network + storage)
// --------------------------------------------------------------------------- //
export type ToWorker =
  | { type: 'ping' }
  | { type: 'capture'; job: CapturedJob }
  | { type: 'match'; description: string; title: string }
  | {
      type: 'draft';
      question: string;
      description: string;
      company: string;
      title: string;
    }
  | { type: 'applied'; fingerprint?: string; url?: string }
  | { type: 'scrape-results'; source: string; jobs: CapturedJob[] }
  | {
      /** Tell FitWright what a form asked. Labels and types only, no values. */
      type: 'report-form';
      fields: {
        label: string;
        field_type: string;
        options: string[];
        filled: boolean;
        matched_key: string | null;
      }[];
      company?: string;
      ats?: string;
      url?: string;
    }
  | {
      /** Remember answers the user typed and explicitly chose to keep. */
      type: 'save-answers';
      answers: { label: string; value: unknown; field_type: string; options: string[] }[];
      company?: string;
      ats?: string;
      url?: string;
    }
  | {
      /**
       * The auto-apply-brain audit trail (Phase 0). Reports where each field's
       * value came from and whether a read-back confirmed it stuck, so grading
       * and "why did it fill that" are answerable without asking a model.
       */
      type: 'record-decisions';
      application_id?: string;
      decisions: {
        site_host: string;
        label: string;
        resolved_target: string | null;
        value_source:
          | 'exact_rule'
          | 'cached_classification'
          | 'brain_classification'
          | 'brain_draft'
          | 'user_answer'
          | 'derived_rule';
        filled: boolean;
        readback_ok: boolean | null;
        required?: boolean;
      }[];
    }
  | {
      type: 'bridge-scrape';
      sites: string[];
      query: string;
      location?: string;
    }
  | { type: 'get-profile' }
  | { type: 'get-queue' }
  | {
      type: 'read-jd';
      /** A single job posting the server could not fetch. */
      url: string;
    }
  | {
      type: 'get-resume-pdf';
      /**
       * The job this form belongs to. Carried so the server can attach the
       * resume tailored for it rather than the master resume.
       */
      company?: string;
      title?: string;
    }
  | { type: 'open-fitwright'; path?: string };

// --------------------------------------------------------------------------- //
// Messages handled by the content script (DOM work)
// --------------------------------------------------------------------------- //
export type ToContent =
  | { type: 'describe-page' }
  | { type: 'autofill' }
  | { type: 'capture-current' }
  | { type: 'scrape-list' }
  | { type: 'preview-fill' };

export type AnyMessage = ToWorker | ToContent;

/** Uniform envelope so every handler reports failure the same way. */
export type Reply<T> = { ok: true; data: T } | { ok: false; error: string };

/**
 * One board's outcome inside a bridge scrape.
 *
 * Named and exported rather than inlined so the worker that produces it and the
 * reply the web app consumes cannot drift apart - they were separately declared
 * duplicates before, which is how a field gets added to one and not the other.
 */
export interface PerSiteResult {
  source: string;
  found: number;
  saved: number;
  error?: string;
  /**
   * Set when we can name why nothing came back:
   * `signed-out` - a login wall (see lib/login-wall.ts)
   * `capped`     - the board's daily allowance is spent (see lib/pacing.ts)
   * `empty`      - the search genuinely matched nothing
   */
  reason?: 'signed-out' | 'capped' | 'empty';
}

/** Per-message reply payloads. */
export interface ReplyMap {
  ping: {
    signedIn: boolean;
    hasResume: boolean;
    versionOk: boolean;
    /** False when a newer extension build exists than the one running. */
    buildCurrent: boolean;
    latestVersion?: string;
  };
  capture: CaptureResponse;
  match: MatchResult;
  draft: DraftResult;
  applied: { updated: boolean };
  'scrape-results': ScrapeResponse;
  'report-form': { seen: number; created: number; updated: number; needs_answer: number };
  'save-answers': { saved: number };
  'record-decisions': { recorded: number; grade: 'green' | 'yellow' | 'red' };
  'bridge-scrape': {
    /** Rows harvested off the boards. */
    total: number;
    /** Rows the backend stored as new; the rest were already in the feed. */
    saved: number;
    perSite: PerSiteResult[];
  };
  'get-profile': import('./types').AutofillProfile;
  'get-resume-pdf': { dataUrl: string; filename: string; tailored: boolean } | null;
  'open-fitwright': null;
  'describe-page': PageContext;
  autofill: {
    filled: number;
    skipped: number;
    questions: string[];
    /**
     * Set when the form filled nothing and we can say why.
     *
     * ``no-application-form`` is the common, non-error case on a job board: the
     * page is a listing, and the form appears only after clicking Apply. It is
     * distinct from ``unrecognised`` below, which means we ARE on a form and
     * genuinely could not read it.
     */
    reason?: 'signed-out' | 'empty' | 'no-application-form';
    /**
     * Fields present but unreadable - a stale adapter rather than a complete
     * form. See content/autofill.ts.
     */
    unrecognised?: number;
  };
  'capture-current': CaptureResponse;
  'scrape-list': { found: number; saved: number; reason?: 'signed-out' | 'empty' };
  'preview-fill': {
    plan: { label: string; value: string }[];
    /**
     * Why the plan is empty, when it is. An empty plan means two opposite things -
     * "already complete" or "no form here" - and reporting both the same way is
     * what made the preview contradict the autofill button on one page.
     */
    reason?: 'no-application-form' | null;
  };
  'get-queue': { items: { company?: string; role?: string }[]; total: number };
  'read-jd': {
    description: string;
    title: string;
    company: string;
    /** Where it came from, so the app can say 'via your browser'. */
    source: string;
  };
}

export function ok<T>(data: T): Reply<T> {
  return { ok: true, data };
}

export function fail<T = never>(error: unknown): Reply<T> {
  const message =
    error instanceof Error ? error.message : typeof error === 'string' ? error : 'Unknown error';
  return { ok: false, error: message };
}

/** Send a message to the service worker and get a typed reply. */
export async function sendToWorker<K extends ToWorker['type']>(
  message: Extract<ToWorker, { type: K }>,
): Promise<Reply<ReplyMap[K]>> {
  try {
    return (await chrome.runtime.sendMessage(message)) as Reply<ReplyMap[K]>;
  } catch (error) {
    // Fires when the worker is asleep mid-send or the extension reloaded.
    return fail(error);
  }
}

/** Send a message to a tab's content script and get a typed reply. */
export async function sendToTab<K extends ToContent['type']>(
  tabId: number,
  message: Extract<ToContent, { type: K }>,
): Promise<Reply<ReplyMap[K]>> {
  try {
    return (await chrome.tabs.sendMessage(tabId, message)) as Reply<ReplyMap[K]>;
  } catch (error) {
    // Most common cause: no content script on this tab (unmatched host).
    return fail(error);
  }
}
