/**
 * Central copy library for AI loading experiences (Loading Experience audit).
 *
 * Stage lists mirror the REAL backend pipeline for each flow; rotating messages
 * are reassurance microcopy. Estimates are static and honest — never countdowns.
 * Keeping this in one place avoids per-page divergence and makes the tone
 * consistent across the product.
 */
import type { AiStage } from '@/components/ai/ai-progress';

export const PARSE_STAGES: AiStage[] = [
  { key: 'uploaded', label: 'Resume uploaded' },
  { key: 'reading', label: 'Reading your document' },
  { key: 'layout', label: 'Understanding the layout' },
  { key: 'extract', label: 'Extracting experience & education' },
  { key: 'skills', label: 'Detecting your skills' },
  { key: 'build', label: 'Building your editable resume' },
];

/**
 * LIVE parse stages, keyed to the REAL backend SSE boundaries emitted by
 * `POST /resumes/upload/stream` (`received` → `extracting` → `structuring`).
 * Used only when the streaming endpoint is available; otherwise the client
 * falls back to the deterministic `PARSE_STAGES` timeline above.
 */
export const PARSE_STREAM_STAGES: AiStage[] = [
  { key: 'received', label: 'Resume received' },
  { key: 'extracting', label: 'Reading your document' },
  { key: 'structuring', label: 'Building your editable resume' },
];

export const PARSE_MESSAGES = [
  'Reading your document…',
  'Understanding your career journey…',
  'Extracting your experience…',
  'Detecting your skills…',
  'We only structure what’s in your file — we never invent details.',
  'Building your editable resume…',
];

export const COVER_LETTER_STAGES: AiStage[] = [
  { key: 'read', label: 'Reading your resume' },
  { key: 'match', label: 'Matching the job' },
  { key: 'draft', label: 'Drafting your letter' },
  { key: 'polish', label: 'Polishing the tone' },
];

export const COVER_LETTER_MESSAGES = [
  'Matching your experience to this role…',
  'Drafting in your voice…',
  'Grounded only in your resume and this job — nothing invented.',
];

export const OUTREACH_STAGES: AiStage[] = [
  { key: 'read', label: 'Reading your resume' },
  { key: 'match', label: 'Matching the role' },
  { key: 'draft', label: 'Writing your message' },
];

export const OUTREACH_MESSAGES = [
  'Finding the strongest hook…',
  'Keeping it concise and specific…',
];

export const INTERVIEW_PREP_STAGES: AiStage[] = [
  { key: 'fit', label: 'Analyzing role fit' },
  { key: 'questions', label: 'Pulling questions from your experience' },
  { key: 'projects', label: 'Preparing project follow-ups' },
  { key: 'gaps', label: 'Identifying areas to prepare' },
];

export const INTERVIEW_PREP_MESSAGES = [
  'Studying the role and your resume…',
  'Drafting likely questions…',
  'Grounded only in your resume and this job — nothing invented.',
];

export const EXPORT_STAGES: AiStage[] = [
  { key: 'prepare', label: 'Preparing your document' },
  { key: 'render', label: 'Rendering your template' },
  { key: 'fonts', label: 'Embedding fonts & images' },
  { key: 'finalize', label: 'Finalizing' },
];

export const EXPORT_MESSAGES = [
  'Preparing your PDF…',
  'Rendering exactly what you see in the preview…',
  'Opening your download shortly…',
];

export const TAILOR_MESSAGES = [
  'Reading the job description…',
  'Matching it to your real experience…',
  'Rewriting your bullet points…',
  'Grounded in your resume — nothing invented.',
  'Scoring ATS compatibility…',
];

export const RESUME_GEN_STAGES: AiStage[] = [
  { key: 'read', label: 'Reading your profile' },
  { key: 'select', label: 'Selecting your strongest content' },
  { key: 'layout', label: 'Laying out your resume' },
  { key: 'finalize', label: 'Finalizing' },
];

export const RESUME_GEN_MESSAGES = [
  'Projecting your profile into a resume…',
  'Choosing the most relevant experience…',
  'Grounded only in your profile — nothing invented.',
];

export const ASK_AI_STAGES: AiStage[] = [
  { key: 'read', label: 'Reading the current content' },
  { key: 'apply', label: 'Applying your instruction' },
  { key: 'write', label: 'Writing the improved version' },
];

export const ASK_AI_MESSAGES = [
  'Rewriting with your instruction…',
  'Grounded in your resume — nothing invented.',
];

// Honest, static estimates (never countdowns).
export const ESTIMATE_SHORT = 'Usually 5–10 seconds.';
export const ESTIMATE_MEDIUM = 'Usually 10–20 seconds.';
export const ESTIMATE_PARSE = 'Usually 5–10 seconds. Larger resumes take a little longer.';
