/**
 * Universal Object Model (Task 3.8 / Req 34)
 *
 * The single canonical graph every screen, route, hook, and future API
 * operates on:
 *
 *   Master Resume
 *   +-- versions[]
 *   +-- Tailored Resume
 *       +-- Job Description
 *       +-- versions[]
 *       +-- Application
 *           +-- status (lifecycle)
 *           +-- Cover Letter
 *           +-- Interview Prep
 *           +-- Outreach
 *           +-- ATS Score
 *
 * These are frontend-facing types. They map onto the existing backend shapes
 * (Resume/Job/Application) without changing any backend contract; new
 * capabilities (versions, search, notifications) attach to nodes here.
 */

export type ResumeProcessingStatus = 'pending' | 'processing' | 'ready' | 'failed';

/** A resume document - master or tailored variant. */
export interface ResumeNode {
  id: string;
  title: string | null;
  isMaster: boolean;
  parentId: string | null; // master a tailored variant descends from
  processingStatus: ResumeProcessingStatus;
  createdAt: string;
  updatedAt: string;
}

/** The job description attached to a tailored resume / application. */
export interface JobDescriptionNode {
  id: string;
  content: string;
  company?: string | null;
  role?: string | null;
}

/** Full application lifecycle (UI enum; maps onto backend status string). */
export const APPLICATION_STAGES = [
  'tailoring',
  'applied',
  'interviewing',
  'offer',
  'accepted',
  'rejected',
  'withdrawn',
  'archived',
] as const;
export type ApplicationStage = (typeof APPLICATION_STAGES)[number];

/** Which stages are terminal (kept off the active board / archivable). */
export const TERMINAL_STAGES: ApplicationStage[] = [
  'accepted',
  'rejected',
  'withdrawn',
  'archived',
];

/** A single job pursuit - the workflow hub object. */
export interface ApplicationNode {
  id: string;
  jobId: string;
  resumeId: string; // the tailored resume for this pursuit
  masterResumeId: string | null;
  stage: ApplicationStage;
  company: string | null;
  role: string | null;
  position: number;
  notes: string | null;
  hasCoverLetter: boolean;
  hasInterviewPrep: boolean;
  hasOutreach: boolean;
  createdAt: string;
  updatedAt: string;
}

/** A stored version of a resume (restore/compare). Snapshots are future backend. */
export interface ResumeVersion {
  id: string;
  resumeId: string;
  label: string;
  source: 'original' | 'ai' | 'manual';
  createdAt: string;
}

/** Change summary for trust UX (Req 15.5). */
export interface ResumeChange {
  path: string;
  before: string;
  after: string;
  status: 'pending' | 'accepted' | 'discarded';
}
export interface ChangeSummary {
  total: number;
  changes: ResumeChange[];
}
