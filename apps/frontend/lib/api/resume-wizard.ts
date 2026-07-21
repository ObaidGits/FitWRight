import type { ResumeData } from '@/components/dashboard/resume-component';
import { apiPost } from './client';
import { readJson } from './errors';

export type ResumeWizardSection =
  | 'intro'
  | 'contact'
  | 'summary'
  | 'workExperience'
  | 'internships'
  | 'education'
  | 'personalProjects'
  | 'skills'
  | 'review';

export type ResumeWizardStep = 'intro' | 'question' | 'review' | 'complete';
export type ResumeWizardAction = 'start' | 'answer' | 'skip' | 'back' | 'review' | 'structured';

/** A deterministic, no-LLM update for a structured section (W-P1.1/W-P1.2/W-P2.1/W-P2.2). */
export interface ResumeWizardStructuredUpdate {
  personal_info?: Record<string, string>;
  technical_skills?: string[];
  education?: EducationInput;
  experiences?: ExperienceInput[];
  projects?: ProjectInput[];
  next_section?: ResumeWizardSection | null;
}

/** Structured Experience entry payload (W-P2.2). */
export interface ExperienceInput {
  title?: string;
  company?: string;
  location?: string;
  years?: string;
  current?: boolean;
  tech?: string[];
  description?: string[];
}

/** Structured Project entry payload (W-P2.2). */
export interface ProjectInput {
  name?: string;
  role?: string;
  years?: string;
  github?: string;
  website?: string;
  tech?: string[];
  description?: string[];
}

export type ResumeWizardAssistKind = 'draft_bullets' | 'parse_entries';

export interface ResumeWizardAssistRequest {
  kind: ResumeWizardAssistKind;
  section: ResumeWizardSection;
  text: string;
  title?: string;
  company?: string;
}

export interface ResumeWizardParsedEntry {
  title?: string;
  company?: string;
  location?: string;
  years?: string;
  name?: string;
  role?: string;
  description?: string[];
}

export interface ResumeWizardAssistResponse {
  bullets: string[];
  entries: ResumeWizardParsedEntry[];
}

/** Structured Education entry payload (W-P2.1). */
export interface EducationInput {
  institution?: string;
  degree?: string;
  specialization?: string;
  location?: string;
  startYear?: string;
  endYear?: string;
  currentlyStudying?: boolean;
  gradeType?: 'cgpa' | 'gpa' | 'percentage' | null;
  score?: string;
  achievements?: string[];
  years?: string;
  description?: string;
}

export interface ResumeSectionConfidence {
  section: string;
  level: 'missing' | 'weak' | 'fair' | 'strong';
}

/** Live deterministic quality scores (W-P2.3). */
export interface ResumeScores {
  completeness: number;
  ats: number;
  sections: ResumeSectionConfidence[];
}

export interface ResumeWizardQuestion {
  text: string;
  section: ResumeWizardSection;
}

export interface ResumeWizardProgress {
  current: number;
  total: number;
}

export interface ResumeWizardHistoryEntry {
  question: string;
  answer: string;
  section: ResumeWizardSection;
  resume_data_before: ResumeData;
}

export interface ResumeWizardState {
  step: ResumeWizardStep;
  resume_data: ResumeData;
  current_question: ResumeWizardQuestion;
  history: ResumeWizardHistoryEntry[];
  asked_count: number;
  inferred_skills: string[];
  is_complete: boolean;
  progress: ResumeWizardProgress;
  warnings: string[];
  /**
   * The answer to the question restored by a `back` action, so the client can
   * repopulate the input for editing (W-P0.1). Empty for every other transition.
   */
  restored_answer?: string;
  /** Live, server-computed quality scores (W-P2.3); read-only. */
  scores?: ResumeScores;
}

export interface ResumeWizardTurnRequest {
  state: ResumeWizardState;
  action: ResumeWizardAction;
  answer?: { text: string };
  structured?: ResumeWizardStructuredUpdate;
}

export interface ResumeWizardTurnResponse {
  state: ResumeWizardState;
}

export interface ResumeWizardFinalizeResponse {
  message: string;
  request_id: string;
  resume_id: string;
  processing_status: 'ready';
  is_master: boolean;
}

export const INTRO_QUESTION =
  "Hi - I'll help you build your master resume. What's your name, and what kind of role are you going for?";

function emptyResumeData(): ResumeData {
  return {
    personalInfo: {
      name: '',
      title: '',
      email: '',
      phone: '',
      location: '',
      website: '',
      linkedin: '',
      github: '',
    },
    summary: '',
    workExperience: [],
    education: [],
    personalProjects: [],
    additional: { technicalSkills: [], languages: [], certificationsTraining: [], awards: [] },
    customSections: {},
    sectionMeta: [],
  };
}

export function createInitialResumeWizardState(): ResumeWizardState {
  return {
    step: 'intro',
    resume_data: emptyResumeData(),
    current_question: { text: INTRO_QUESTION, section: 'intro' },
    history: [],
    asked_count: 0,
    inferred_skills: [],
    is_complete: false,
    // Fixed milestone denominator (Identity, Contact, Experience, Education,
    // Skills, Summary) so the goal never recedes while answering (W-P0.3).
    progress: { current: 0, total: 6 },
    warnings: [],
    restored_answer: '',
  };
}

export async function postResumeWizardTurn(
  payload: ResumeWizardTurnRequest
): Promise<ResumeWizardTurnResponse> {
  const response = await apiPost('/resume-wizard/turn', payload);
  // readJson normalizes the backend error envelope / `detail` into a clean,
  // human-readable message (never a raw JSON body shown to the user).
  return readJson<ResumeWizardTurnResponse>(response, 'The wizard could not continue.');
}

/**
 * Fetch the opening state, prefilled from the user's profile when one exists
 * (W-P3.2). Best-effort: resolves to `null` on any failure so the wizard can
 * fall back to the client-side empty initial state without surfacing an error.
 */
export async function prefillResumeWizard(): Promise<ResumeWizardState | null> {
  try {
    const response = await apiPost('/resume-wizard/turn', {
      state: createInitialResumeWizardState(),
      action: 'start',
    });
    if (!response.ok) return null;
    const body = (await response.json()) as ResumeWizardTurnResponse;
    return body.state ?? null;
  } catch {
    return null;
  }
}

/**
 * Focused AI assist for the hybrid Experience/Project cards (W-P2.2): draft
 * bullets from a plain description, or parse a pasted blob into structured
 * entries. Returns content for confirmation; never mutates wizard state.
 */
export async function assistResumeWizard(
  payload: ResumeWizardAssistRequest
): Promise<ResumeWizardAssistResponse> {
  const response = await apiPost('/resume-wizard/assist', payload);
  return readJson<ResumeWizardAssistResponse>(response, 'AI assist failed. Please try again.');
}

/**
 * Save the wizard draft as a resume. ``isMaster`` is the user's intent:
 * `true` sets it as the master (only when none exists), `false` saves a regular
 * resume, `undefined` lets the server default (master only if the user has none).
 */
export async function finalizeResumeWizard(
  state: ResumeWizardState,
  isMaster?: boolean
): Promise<ResumeWizardFinalizeResponse> {
  const response = await apiPost('/resume-wizard/finalize', { state, is_master: isMaster });
  return readJson<ResumeWizardFinalizeResponse>(response, 'Could not save your resume.');
}
