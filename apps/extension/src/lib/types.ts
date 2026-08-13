/**
 * Types shared across the extension.
 *
 * The wire types mirror `apps/backend/app/routers/extension.py`. Keep them in
 * sync with that module - it is the source of truth for the contract, and
 * `API_VERSION` below is what the handshake checks against.
 */

/** Wire contract version this build speaks. Must match the backend's. */
export const API_VERSION = 1;

// --------------------------------------------------------------------------- //
// Jobs
// --------------------------------------------------------------------------- //

/** A job as extracted from a live page, before FitWright normalizes it. */
export interface CapturedJob {
  title: string;
  company: string;
  location: string;
  url: string;
  source: string;
  description?: string | null;
  salary?: string | null;
  posted_at?: string | null;
  is_remote?: boolean | null;
}

export interface CaptureResponse {
  saved: number;
  duplicate: boolean;
  fingerprint: string;
}

export interface ScrapeResponse {
  received: number;
  saved: number;
  source: string;
}

// --------------------------------------------------------------------------- //
// Match + drafting
// --------------------------------------------------------------------------- //

export interface MatchResult {
  match_score: number;
  matched: string[];
  missing: string[];
  resume_id: string | null;
  degraded: boolean;
}

export interface DraftResult {
  answer: string;
  degraded: boolean;
}

// --------------------------------------------------------------------------- //
// Profile
// --------------------------------------------------------------------------- //

/** Everything needed to fill a standard application form. */
export interface AutofillProfile {
  full_name: string;
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  location: string;
  linkedin: string;
  github: string;
  website: string;
  current_title: string;
  current_company: string;
  years_experience: number | null;
  /** Structured address - ATS forms ask for these parts separately. */
  address_line1: string;
  address_line2: string;
  city: string;
  state: string;
  postal_code: string;
  country: string;
  /**
   * Eligibility answers, served from the user's FitWright Profile. Blank means
   * unanswered, and must be left blank on the form rather than guessed: a wrong
   * visa status or salary auto-rejects the application.
   */
  work_authorization: string;
  visa_status: string;
  notice_period: string;
  salary_expectation: string;
  /** Tri-state: null is unanswered, not "no". */
  willing_to_relocate: boolean | null;
  availability: string;
  remote_preference: string;
  highest_degree: string;
  highest_institution: string;
  education_years: string;
  resume_id: string | null;
  resume_filename: string;
  resume_pdf_path: string | null;
  preferences: Record<string, unknown>;
}

export interface PingResult {
  ok: boolean;
  api_version: number;
  user_id: string;
  has_resume: boolean;
  resume_count: number;
}

// --------------------------------------------------------------------------- //
// Local settings (extension-owned, never derived from the resume)
// --------------------------------------------------------------------------- //

/**
 * Answers a resume cannot supply. These live only in `chrome.storage.sync` so
 * the user controls them, and they are the reason the autofill profile is a
 * merge of server data + local answers rather than one server object.
 */
export interface LocalPreferences {
  workAuthorization: string;
  requiresSponsorship: string;
  noticePeriod: string;
  salaryExpectation: string;
  gender: string;
  ethnicity: string;
  veteranStatus: string;
  disabilityStatus: string;
  /** Free-form extras keyed by a normalized question label. */
  custom: Record<string, string>;
}

/**
 * One saved search for background scraping.
 *
 * A bare query string is not enough: each board needs its own search URL shape,
 * so the board id travels with the terms.
 */
export interface ScrapeQuery {
  /** Adapter id, e.g. 'indeed' | 'linkedin' | 'instahyre' | 'hirist' | 'foundit' */
  source: string;
  query: string;
  location?: string;
}

export interface ExtensionSettings {
  /** FitWright base URL, e.g. http://localhost:3000 */
  apiBaseUrl: string;
  /** Show the floating match badge on job pages. */
  showBadge: boolean;
  /** Auto-capture a job to the feed the first time its page is opened. */
  autoCapture: boolean;
  /** Watch for form submissions and mark the job applied. */
  trackApplications: boolean;
  /** Scheduled background scraping of the boards the server cannot reach. */
  backgroundScrape: boolean;
  /** Minutes between background scrape runs. */
  scrapeIntervalMinutes: number;
  /** Saved searches used by background scraping. */
  scrapeQueries: ScrapeQuery[];
  preferences: LocalPreferences;
}

export const DEFAULT_PREFERENCES: LocalPreferences = {
  workAuthorization: '',
  requiresSponsorship: '',
  noticePeriod: '',
  salaryExpectation: '',
  gender: '',
  ethnicity: '',
  veteranStatus: '',
  disabilityStatus: '',
  custom: {},
};

export const DEFAULT_SETTINGS: ExtensionSettings = {
  apiBaseUrl: 'http://localhost:3000',
  showBadge: true,
  // Off by default: silently writing to the user's feed on every page view is
  // a surprise. The popup's Save button is the explicit path.
  autoCapture: false,
  trackApplications: true,
  backgroundScrape: false,
  scrapeIntervalMinutes: 360,
  scrapeQueries: [],
  preferences: DEFAULT_PREFERENCES,
};

// --------------------------------------------------------------------------- //
// Page classification
// --------------------------------------------------------------------------- //

/** What the content script decided the current page is. */
export type PageKind =
  | 'job-posting' // a single job we can capture + score
  | 'application-form' // a form we can autofill
  | 'job-list' // search results we can scrape in bulk
  | 'unknown';

export interface PageContext {
  kind: PageKind;
  adapter: string;
  job: CapturedJob | null;
  /** True when the page also has a fillable application form. */
  hasForm: boolean;
}
