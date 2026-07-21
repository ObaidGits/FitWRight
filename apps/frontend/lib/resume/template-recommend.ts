/**
 * Smart template recommendations (pure, deterministic).
 *
 * Ranks the catalog against a light signal derived from the user's resume /
 * profile - role, industry keywords, experience level, skills, country - so the
 * gallery can surface "Recommended for you" without any LLM call. Scoring is
 * transparent and every recommendation carries a human reason.
 */
import type { ResumeData } from '@/components/dashboard/resume-component';
import {
  type ExperienceLevel,
  type ResumeTemplate,
  RESUME_TEMPLATES,
} from '@/lib/resume/template-catalog';

export interface RecommendSignal {
  role?: string;
  industry?: string;
  experienceLevel?: ExperienceLevel;
  skills?: string[];
  country?: string;
}

export interface ScoredTemplate {
  template: ResumeTemplate;
  score: number;
  reasons: string[];
}

const SENIOR_WORDS = ['senior', 'sr.', 'lead', 'principal', 'staff'];
const EXEC_WORDS = ['head', 'director', 'vp', 'vice president', 'chief', 'cto', 'ceo', 'cfo'];
const STUDENT_WORDS = ['intern', 'student', 'graduate', 'trainee', 'fresher'];

/** Infer a rough experience level from a resume (count + title seniority). */
export function experienceLevelFromResume(data: ResumeData): ExperienceLevel {
  const title = (data.personalInfo?.title ?? '').toLowerCase();
  if (EXEC_WORDS.some((w) => title.includes(w))) return 'executive';
  if (SENIOR_WORDS.some((w) => title.includes(w))) return 'senior';
  if (STUDENT_WORDS.some((w) => title.includes(w))) return 'student';
  const count = data.workExperience?.length ?? 0;
  if (count === 0) return 'student';
  if (count <= 1) return 'entry';
  if (count <= 3) return 'mid';
  return 'senior';
}

/** Build a recommendation signal from a resume's structured data. */
export function signalFromResume(data: ResumeData): RecommendSignal {
  return {
    role: data.personalInfo?.title ?? undefined,
    experienceLevel: experienceLevelFromResume(data),
    skills: (data.additional?.technicalSkills ?? []).filter(
      (s): s is string => typeof s === 'string' && s.trim() !== ''
    ),
    country: undefined,
  };
}

function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^a-z0-9+#.]+/)
    .filter((t) => t.length > 1);
}

/** Score a single template against the signal (transparent + reason-carrying). */
export function scoreTemplate(t: ResumeTemplate, signal: RecommendSignal): ScoredTemplate {
  let score = 0;
  const reasons: string[] = [];
  const haystack = [...t.recommendedFor, ...t.tags, ...t.industries].map((s) => s.toLowerCase());
  const inHay = (term: string) => haystack.some((h) => h.includes(term) || term.includes(h));

  if (signal.role) {
    const roleTokens = tokenize(signal.role);
    const roleHit = roleTokens.some((tok) => inHay(tok));
    if (roleHit) {
      score += 3;
      reasons.push(`Matches your role "${signal.role}"`);
    }
  }

  if (signal.industry && inHay(signal.industry.toLowerCase())) {
    score += 3;
    reasons.push(`Fits ${signal.industry}`);
  }

  if (signal.experienceLevel && t.experienceLevels.includes(signal.experienceLevel)) {
    score += 2;
    reasons.push(`Suited to ${signal.experienceLevel}-level candidates`);
  }

  if (signal.skills && signal.skills.length > 0) {
    let overlap = 0;
    for (const skill of signal.skills) {
      const tokens = tokenize(skill);
      if (tokens.some((tok) => inHay(tok))) overlap += 1;
      if (overlap >= 3) break;
    }
    if (overlap > 0) {
      score += overlap;
      reasons.push('Aligns with your skills');
    }
  }

  if (signal.country && t.countries.includes(signal.country)) {
    score += 1;
    reasons.push(`Common in ${signal.country}`);
  }

  // Gentle bias toward ATS-safe, popular templates as a tiebreaker.
  score += t.atsScore * 0.4 + t.popularity / 100;
  if (t.atsScore >= 5 && reasons.length > 0) reasons.push('Excellent ATS compatibility');

  return { template: t, score, reasons };
}

export function recommendTemplates(
  signal: RecommendSignal,
  templates: ResumeTemplate[] = RESUME_TEMPLATES,
  limit = 6
): ScoredTemplate[] {
  return templates
    .map((t) => scoreTemplate(t, signal))
    .sort((a, b) => b.score - a.score || b.template.popularity - a.template.popularity)
    .slice(0, limit);
}
