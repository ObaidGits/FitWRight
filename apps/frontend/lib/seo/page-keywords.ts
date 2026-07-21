/**
 * Keyword research -> page mapping for FitWright.
 *
 * Keywords are grouped by search intent and assigned to a single primary page
 * to avoid cannibalization. The landing page owns the highest-value commercial
 * and transactional terms; supporting pages own their own intent clusters.
 *
 * These arrays feed `keywords` in each page's metadata. They are intentionally
 * truthful to the product's real capabilities - no stuffing, no invented terms.
 */

export const KEYWORDS = {
  /** Landing / home - broad brand + builder head terms. The tailoring cluster
      lives on its own page (below) to avoid home <-> /resume-tailoring overlap. */
  landing: [
    'AI resume builder',
    'ATS resume builder',
    'resume optimizer',
    'resume generator',
    'resume analyzer',
    'open source resume builder',
    'free resume builder',
    'privacy-first resume builder',
    'bring your own API key resume',
    'job description analyzer',
    'developer resume builder',
    'software engineer resume builder',
    'student resume builder',
  ],
  /** Resume tailoring landing - owns the tailoring intent cluster. */
  tailoring: [
    'resume tailoring',
    'AI resume tailoring',
    'tailor resume to job description',
    'customize resume for each job',
    'resume keyword matching',
    'match resume to job posting',
    'AI resume customization',
  ],
  /** ATS resume checker landing - owns the ATS scoring/checking cluster. */
  atsChecker: [
    'ATS resume checker',
    'ATS resume scanner',
    'resume ATS score',
    'check resume against job',
    'ATS compatibility checker',
    'resume keyword scanner',
    'applicant tracking system checker',
  ],
  /** Cover letter generator landing - owns the cover letter cluster. */
  coverLetter: [
    'cover letter generator',
    'AI cover letter generator',
    'tailored cover letter',
    'cover letter writer',
    'generate cover letter from resume',
    'job specific cover letter',
  ],
  /** Interview preparation landing - owns the interview prep cluster. */
  interviewPrep: [
    'interview preparation',
    'interview prep tool',
    'AI interview questions',
    'resume based interview questions',
    'interview practice questions',
    'job interview preparation',
  ],
  /** Connect - developer branding / navigational. Distinct from `contact` to
      avoid cannibalization: `connect` owns collaboration/feedback intent. */
  connect: [
    'FitWright developer',
    'AI engineer collaboration',
    'open source contribution',
    'product feedback FitWright',
    'give feedback to FitWright',
  ],
  /** Contact - navigational / transactional (hire / collaborate). */
  contact: [
    'contact FitWright',
    'hire AI engineer',
    'full-stack developer contact',
    'FitWright support',
  ],
  /** Privacy - informational / trust. */
  privacy: ['FitWright privacy policy', 'resume data privacy', 'AI resume data security'],
  /** Terms - informational / trust. */
  terms: ['FitWright terms of use', 'resume builder terms', 'Apache 2.0 license'],
} as const;

export type KeywordGroup = keyof typeof KEYWORDS;
