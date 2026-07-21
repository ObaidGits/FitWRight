/**
 * Global Resume Template Catalog (metadata-driven).
 *
 * A catalog template is NOT a new renderer - it is a curated *preset* over the
 * existing layout engines (`TemplateType`) plus rich, structured metadata
 * (category, industry, experience level, country, ATS score, photo behavior,
 * recommendations). This is the whole point: the app has a small set of proven
 * layout engines + the shared WYSIWYG renderer, and the catalog composes them
 * with typography / density / accent / photo settings into many meaningfully
 * distinct, purpose-built templates.
 *
 * Scalability contract: adding a template = appending ONE entry here (metadata
 * + engine + settings preset). No core rendering, PDF, or preview code changes.
 */
import {
  type TemplateSettings,
  type TemplateType,
  DEFAULT_TEMPLATE_SETTINGS,
  applyTemplatePreset,
} from '@/lib/types/template-settings';
import { photoCapability } from '@/lib/types/template-capabilities';
import type { PhotoPosition } from '@/lib/types/photo';

export type TemplateCategory =
  | 'ats'
  | 'professional'
  | 'technology'
  | 'creative'
  | 'academic'
  | 'career-stage'
  | 'international';

export type ExperienceLevel = 'student' | 'entry' | 'mid' | 'senior' | 'executive';

/** How a template treats a profile photo (the renderer adapts automatically). */
export type PhotoSupport = 'none' | 'supported' | 'required';

export interface ResumeTemplate {
  /** Stable slug/id (URL-safe, unique). */
  id: string;
  name: string;
  category: TemplateCategory;
  /** Layout engine this preset renders through (shared renderer - no dup). */
  engine: TemplateType;
  /** Preset applied over the engine defaults (accent/fonts/spacing/page/etc.). */
  settings: Partial<TemplateSettings>;
  photoSupport: PhotoSupport;
  /** Preferred photo slot when a photo is shown (defaults to the engine's). */
  photoPosition?: PhotoPosition;
  /** 1-5 ATS friendliness with a short, honest reason. */
  atsScore: 1 | 2 | 3 | 4 | 5;
  atsNote: string;
  industries: string[];
  experienceLevels: ExperienceLevel[];
  /** ISO-ish country tags for regional conventions (empty = universal). */
  countries: string[];
  /** Persona/role tags used by search + recommendations. */
  recommendedFor: string[];
  tags: string[];
  /** Editorial popularity weight (0-100) for the default sort. */
  popularity: number;
  /** Metadata/preset version for migration + backward compatibility. */
  version: number;
  description: string;
}

// ---------------------------------------------------------------------------
// The catalog. Grouped by category; each entry is a distinct, purposeful preset.
// ---------------------------------------------------------------------------

export const RESUME_TEMPLATES: ResumeTemplate[] = [
  // ---- ATS-optimized (no photo, maximum parseability) --------------------
  {
    id: 'ats-classic',
    name: 'ATS Classic',
    category: 'ats',
    engine: 'swiss-single',
    settings: { accentColor: 'blue', spacing: { section: 3, item: 2, lineHeight: 3 } },
    photoSupport: 'none',
    atsScore: 5,
    atsNote: 'Single column, standard headings - parses cleanly in every ATS.',
    industries: ['general', 'operations', 'administration'],
    experienceLevels: ['entry', 'mid', 'senior'],
    countries: ['US', 'CA', 'AU'],
    recommendedFor: ['ats', 'safe choice', 'recruiter-friendly'],
    tags: ['ats', 'simple', 'classic', 'single-column'],
    popularity: 96,
    version: 1,
    description: 'The dependable single-column standard that any ATS reads without a hitch.',
  },
  {
    id: 'ats-modern',
    name: 'ATS Modern',
    category: 'ats',
    engine: 'clean',
    settings: { accentColor: 'blue', spacing: { section: 4, item: 3, lineHeight: 3 } },
    photoSupport: 'none',
    atsScore: 5,
    atsNote: 'Clean sans layout with roomy headings; still fully machine-readable.',
    industries: ['general', 'technology', 'marketing'],
    experienceLevels: ['entry', 'mid', 'senior'],
    countries: ['US', 'CA'],
    recommendedFor: ['ats', 'modern', 'clean'],
    tags: ['ats', 'modern', 'minimal', 'sans'],
    popularity: 92,
    version: 1,
    description: 'A contemporary, airy take on the ATS-safe resume.',
  },
  {
    id: 'ats-executive',
    name: 'ATS Executive',
    category: 'ats',
    engine: 'swiss-single',
    settings: {
      accentColor: 'blue',
      spacing: { section: 4, item: 3, lineHeight: 4 },
      fontSize: { base: 4, headerScale: 4, headerFont: 'serif', bodyFont: 'serif' },
    },
    photoSupport: 'none',
    atsScore: 5,
    atsNote: 'Serif, generous spacing for senior scannability - still ATS-clean.',
    industries: ['general', 'finance', 'operations'],
    experienceLevels: ['senior', 'executive'],
    countries: ['US', 'UK', 'CA'],
    recommendedFor: ['executive', 'director', 'senior leadership'],
    tags: ['ats', 'executive', 'serif', 'senior'],
    popularity: 84,
    version: 1,
    description: 'A weighty, serif-led ATS layout tuned for senior and executive roles.',
  },
  {
    id: 'ats-minimal',
    name: 'ATS Minimal',
    category: 'ats',
    engine: 'clean',
    settings: {
      accentColor: 'blue',
      compactMode: true,
      spacing: { section: 2, item: 2, lineHeight: 2 },
    },
    photoSupport: 'none',
    atsScore: 5,
    atsNote: 'Dense, no-frills layout that fits more on one page and parses perfectly.',
    industries: ['general', 'technology'],
    experienceLevels: ['entry', 'mid'],
    countries: ['US', 'CA', 'AU'],
    recommendedFor: ['one-page', 'concise', 'ats'],
    tags: ['ats', 'minimal', 'compact', 'one-page'],
    popularity: 80,
    version: 1,
    description: 'Maximum content density for a tight, single-page ATS resume.',
  },

  // ---- Professional -------------------------------------------------------
  {
    id: 'corporate-professional',
    name: 'Corporate Professional',
    category: 'professional',
    engine: 'swiss-two-column',
    settings: { accentColor: 'blue' },
    photoSupport: 'supported',
    photoPosition: 'sidebar',
    atsScore: 4,
    atsNote: 'Two-column with a skills sidebar - great for humans, good for ATS.',
    industries: ['business', 'operations', 'sales'],
    experienceLevels: ['mid', 'senior'],
    countries: ['US', 'UK', 'CA', 'AU'],
    recommendedFor: ['corporate', 'business', 'manager'],
    tags: ['professional', 'two-column', 'corporate'],
    popularity: 88,
    version: 1,
    description: 'A polished corporate two-column layout with a skills sidebar.',
  },
  {
    id: 'business-executive',
    name: 'Business Executive',
    category: 'professional',
    engine: 'clean',
    settings: {
      accentColor: 'blue',
      spacing: { section: 4, item: 3, lineHeight: 4 },
      fontSize: { base: 3, headerScale: 4, headerFont: 'serif', bodyFont: 'sans-serif' },
    },
    photoSupport: 'supported',
    photoPosition: 'header-right',
    atsScore: 4,
    atsNote: 'Serif headers over a clean body; refined without confusing parsers.',
    industries: ['business', 'consulting', 'operations'],
    experienceLevels: ['senior', 'executive'],
    countries: ['US', 'UK'],
    recommendedFor: ['executive', 'VP', 'leadership'],
    tags: ['professional', 'executive', 'refined'],
    popularity: 82,
    version: 1,
    description: 'An understated executive layout with serif headings and calm spacing.',
  },
  {
    id: 'consulting',
    name: 'Consulting',
    category: 'professional',
    engine: 'swiss-single',
    settings: { accentColor: 'blue', spacing: { section: 3, item: 2, lineHeight: 3 } },
    photoSupport: 'none',
    atsScore: 5,
    atsNote: 'Impact-first single column favored by consulting and MBA recruiters.',
    industries: ['consulting', 'strategy', 'business'],
    experienceLevels: ['mid', 'senior'],
    countries: ['US', 'UK'],
    recommendedFor: ['consulting', 'strategy', 'mba'],
    tags: ['professional', 'consulting', 'impact'],
    popularity: 79,
    version: 1,
    description: 'A results-led single-column format tuned for consulting applications.',
  },
  {
    id: 'finance-banking',
    name: 'Finance & Banking',
    category: 'professional',
    engine: 'latex',
    settings: { accentColor: 'blue' },
    photoSupport: 'none',
    atsScore: 5,
    atsNote: 'Conservative serif layout expected in finance and banking.',
    industries: ['finance', 'banking', 'accounting'],
    experienceLevels: ['entry', 'mid', 'senior'],
    countries: ['US', 'UK'],
    recommendedFor: ['finance', 'banking', 'analyst'],
    tags: ['professional', 'finance', 'serif', 'conservative'],
    popularity: 75,
    version: 1,
    description: 'A conservative, ruled serif layout that fits finance conventions.',
  },
  {
    id: 'management',
    name: 'Management',
    category: 'professional',
    engine: 'swiss-two-column',
    settings: { accentColor: 'green' },
    photoSupport: 'supported',
    photoPosition: 'sidebar',
    atsScore: 4,
    atsNote: 'Two-column layout highlighting leadership and outcomes.',
    industries: ['management', 'operations', 'product'],
    experienceLevels: ['mid', 'senior'],
    countries: ['US', 'UK', 'CA'],
    recommendedFor: ['manager', 'team lead', 'operations'],
    tags: ['professional', 'management', 'two-column'],
    popularity: 72,
    version: 1,
    description: 'A management-focused two-column layout with a calm green accent.',
  },

  // ---- Technology ---------------------------------------------------------
  {
    id: 'software-engineer',
    name: 'Software Engineer',
    category: 'technology',
    engine: 'modern',
    settings: { accentColor: 'blue' },
    photoSupport: 'supported',
    photoPosition: 'header-left',
    atsScore: 4,
    atsNote: 'Accent header with a clean body - modern but still parseable.',
    industries: ['technology', 'software'],
    experienceLevels: ['entry', 'mid', 'senior'],
    countries: ['US', 'CA', 'EU'],
    recommendedFor: ['software engineer', 'developer', 'programmer'],
    tags: ['technology', 'developer', 'modern'],
    popularity: 94,
    version: 1,
    description: 'A modern engineering resume with an accented header and clean body.',
  },
  {
    id: 'frontend-developer',
    name: 'Frontend Developer',
    category: 'technology',
    engine: 'modern',
    settings: { accentColor: 'orange' },
    photoSupport: 'supported',
    photoPosition: 'header-left',
    atsScore: 4,
    atsNote: 'Modern accent layout with a touch of warmth for UI-facing roles.',
    industries: ['technology', 'software', 'web'],
    experienceLevels: ['entry', 'mid', 'senior'],
    countries: ['US', 'CA', 'EU'],
    recommendedFor: ['frontend', 'react', 'ui engineer', 'web developer'],
    tags: ['technology', 'frontend', 'modern'],
    popularity: 86,
    version: 1,
    description: 'A modern layout with a warm accent, aimed at frontend engineers.',
  },
  {
    id: 'backend-developer',
    name: 'Backend Developer',
    category: 'technology',
    engine: 'modern-two-column',
    settings: {
      accentColor: 'blue',
      fontSize: { base: 3, headerScale: 3, headerFont: 'mono', bodyFont: 'sans-serif' },
    },
    photoSupport: 'supported',
    photoPosition: 'sidebar',
    atsScore: 3,
    atsNote: 'Two-column with a monospace header nod; keep to one page for ATS.',
    industries: ['technology', 'software', 'infrastructure'],
    experienceLevels: ['mid', 'senior'],
    countries: ['US', 'CA', 'EU'],
    recommendedFor: ['backend', 'api', 'systems', 'server'],
    tags: ['technology', 'backend', 'two-column', 'mono'],
    popularity: 78,
    version: 1,
    description: 'A two-column developer layout with a subtle monospace signature.',
  },
  {
    id: 'fullstack-developer',
    name: 'Full-Stack Developer',
    category: 'technology',
    engine: 'modern',
    settings: { accentColor: 'green' },
    photoSupport: 'supported',
    photoPosition: 'header-left',
    atsScore: 4,
    atsNote: 'Balanced modern layout for broad, cross-stack experience.',
    industries: ['technology', 'software'],
    experienceLevels: ['mid', 'senior'],
    countries: ['US', 'CA', 'EU'],
    recommendedFor: ['full stack', 'mern', 'generalist engineer'],
    tags: ['technology', 'fullstack', 'modern'],
    popularity: 83,
    version: 1,
    description: 'A balanced modern resume for full-stack engineers.',
  },
  {
    id: 'devops-cloud',
    name: 'DevOps & Cloud',
    category: 'technology',
    engine: 'modern-two-column',
    settings: { accentColor: 'blue' },
    photoSupport: 'supported',
    photoPosition: 'sidebar',
    atsScore: 3,
    atsNote: 'Sidebar surfaces tooling/skills; keep it concise for ATS.',
    industries: ['technology', 'infrastructure', 'cloud'],
    experienceLevels: ['mid', 'senior'],
    countries: ['US', 'CA', 'EU'],
    recommendedFor: ['devops', 'sre', 'cloud engineer', 'platform'],
    tags: ['technology', 'devops', 'cloud', 'two-column'],
    popularity: 74,
    version: 1,
    description: 'A tooling-forward two-column layout for DevOps and cloud roles.',
  },
  {
    id: 'data-scientist',
    name: 'Data Scientist',
    category: 'technology',
    engine: 'clean',
    settings: { accentColor: 'green', spacing: { section: 3, item: 3, lineHeight: 3 } },
    photoSupport: 'none',
    atsScore: 4,
    atsNote: 'Clean layout that gives room to projects, metrics, and publications.',
    industries: ['technology', 'data', 'research'],
    experienceLevels: ['entry', 'mid', 'senior'],
    countries: ['US', 'CA', 'EU'],
    recommendedFor: ['data scientist', 'ml', 'analytics'],
    tags: ['technology', 'data', 'clean'],
    popularity: 81,
    version: 1,
    description: 'A clean, metric-friendly layout for data and ML practitioners.',
  },
  {
    id: 'ai-engineer',
    name: 'AI Engineer',
    category: 'technology',
    engine: 'modern',
    settings: { accentColor: 'blue' },
    photoSupport: 'supported',
    photoPosition: 'header-left',
    atsScore: 4,
    atsNote: 'Modern layout with space for research, models, and impact.',
    industries: ['technology', 'ai', 'research'],
    experienceLevels: ['mid', 'senior'],
    countries: ['US', 'CA', 'EU'],
    recommendedFor: ['ai engineer', 'machine learning', 'llm', 'ml engineer'],
    tags: ['technology', 'ai', 'modern'],
    popularity: 85,
    version: 1,
    description: 'A modern resume tuned for AI/ML engineering roles.',
  },
  {
    id: 'cybersecurity',
    name: 'Cybersecurity',
    category: 'technology',
    engine: 'latex',
    settings: {
      accentColor: 'blue',
      fontSize: { base: 3, headerScale: 3, headerFont: 'mono', bodyFont: 'mono' },
    },
    photoSupport: 'none',
    atsScore: 4,
    atsNote: 'Precise monospace-leaning layout for security and infra roles.',
    industries: ['technology', 'security', 'infrastructure'],
    experienceLevels: ['mid', 'senior'],
    countries: ['US', 'CA', 'EU'],
    recommendedFor: ['security', 'infosec', 'pentester', 'soc'],
    tags: ['technology', 'security', 'mono'],
    popularity: 70,
    version: 1,
    description: 'A precise, monospace-flavored layout for cybersecurity professionals.',
  },

  // ---- Creative -----------------------------------------------------------
  {
    id: 'designer',
    name: 'Designer',
    category: 'creative',
    engine: 'vivid',
    settings: { accentColor: 'orange' },
    photoSupport: 'supported',
    photoPosition: 'sidebar',
    atsScore: 3,
    atsNote: 'Expressive two-column layout; use the ATS set for strict applications.',
    industries: ['design', 'creative'],
    experienceLevels: ['entry', 'mid', 'senior'],
    countries: ['US', 'EU', 'UK'],
    recommendedFor: ['designer', 'graphic', 'visual', 'brand'],
    tags: ['creative', 'designer', 'vivid', 'two-column'],
    popularity: 77,
    version: 1,
    description: 'A vivid, expressive layout for designers and visual creatives.',
  },
  {
    id: 'ux-product-designer',
    name: 'UI/UX & Product Designer',
    category: 'creative',
    engine: 'vivid',
    settings: { accentColor: 'blue' },
    photoSupport: 'supported',
    photoPosition: 'sidebar',
    atsScore: 3,
    atsNote: 'Portfolio-friendly layout that foregrounds process and impact.',
    industries: ['design', 'product', 'technology'],
    experienceLevels: ['entry', 'mid', 'senior'],
    countries: ['US', 'EU', 'UK'],
    recommendedFor: ['ux', 'ui', 'product designer', 'interaction'],
    tags: ['creative', 'ux', 'product', 'vivid'],
    popularity: 76,
    version: 1,
    description: 'A process-forward layout for UX, UI, and product designers.',
  },
  {
    id: 'marketing-content',
    name: 'Marketing & Content',
    category: 'creative',
    engine: 'modern-two-column',
    settings: { accentColor: 'red' },
    photoSupport: 'supported',
    photoPosition: 'sidebar',
    atsScore: 3,
    atsNote: 'Bold accent two-column layout for marketing and content roles.',
    industries: ['marketing', 'content', 'communications'],
    experienceLevels: ['entry', 'mid', 'senior'],
    countries: ['US', 'EU', 'UK'],
    recommendedFor: ['marketing', 'content', 'social', 'copywriter'],
    tags: ['creative', 'marketing', 'bold'],
    popularity: 73,
    version: 1,
    description: 'A bold two-column layout for marketing and content professionals.',
  },

  // ---- Academic -----------------------------------------------------------
  {
    id: 'academic-research',
    name: 'Academic & Research',
    category: 'academic',
    engine: 'latex',
    settings: { accentColor: 'blue', spacing: { section: 4, item: 3, lineHeight: 4 } },
    photoSupport: 'none',
    atsScore: 5,
    atsNote: 'Serif academic CV format with room for publications and grants.',
    industries: ['academia', 'research', 'science'],
    experienceLevels: ['mid', 'senior', 'executive'],
    countries: ['US', 'UK', 'EU'],
    recommendedFor: ['researcher', 'professor', 'phd', 'postdoc'],
    tags: ['academic', 'research', 'cv', 'serif'],
    popularity: 71,
    version: 1,
    description: 'A traditional academic CV layout for research and faculty roles.',
  },
  {
    id: 'graduate-student',
    name: 'Graduate Student',
    category: 'academic',
    engine: 'swiss-single',
    settings: { accentColor: 'blue', spacing: { section: 3, item: 2, lineHeight: 3 } },
    photoSupport: 'none',
    atsScore: 5,
    atsNote: 'Clean single column that highlights coursework, projects, and research.',
    industries: ['academia', 'research', 'general'],
    experienceLevels: ['student', 'entry'],
    countries: ['US', 'UK', 'EU', 'CA'],
    recommendedFor: ['graduate', 'phd student', 'masters'],
    tags: ['academic', 'student', 'single-column'],
    popularity: 68,
    version: 1,
    description: 'A focused single-column layout for graduate and PhD students.',
  },

  // ---- Career stage -------------------------------------------------------
  {
    id: 'student-fresher',
    name: 'Student & Fresher',
    category: 'career-stage',
    engine: 'clean',
    settings: { accentColor: 'green', spacing: { section: 3, item: 3, lineHeight: 3 } },
    photoSupport: 'none',
    atsScore: 5,
    atsNote: 'Approachable clean layout that fills a page from limited experience.',
    industries: ['general', 'technology', 'business'],
    experienceLevels: ['student', 'entry'],
    countries: ['US', 'CA', 'IN', 'AU'],
    recommendedFor: ['student', 'fresher', 'internship', 'new grad'],
    tags: ['career-stage', 'student', 'entry', 'clean'],
    popularity: 87,
    version: 1,
    description: 'A friendly, well-proportioned layout for students and first jobs.',
  },
  {
    id: 'senior-executive',
    name: 'Senior Executive',
    category: 'career-stage',
    engine: 'clean',
    settings: {
      accentColor: 'blue',
      spacing: { section: 5, item: 3, lineHeight: 4 },
      fontSize: { base: 3, headerScale: 4, headerFont: 'serif', bodyFont: 'sans-serif' },
    },
    photoSupport: 'none',
    atsScore: 5,
    atsNote: 'Spacious, authoritative layout for extensive leadership history.',
    industries: ['business', 'operations', 'general'],
    experienceLevels: ['senior', 'executive'],
    countries: ['US', 'UK', 'CA'],
    recommendedFor: ['executive', 'c-level', 'director', 'vp'],
    tags: ['career-stage', 'executive', 'senior', 'spacious'],
    popularity: 80,
    version: 1,
    description: 'A commanding, spacious layout for senior and executive careers.',
  },

  // ---- International ------------------------------------------------------
  {
    id: 'europe-cv',
    name: 'Europe CV (with photo)',
    category: 'international',
    engine: 'swiss-two-column',
    settings: { accentColor: 'blue' },
    photoSupport: 'required',
    photoPosition: 'sidebar',
    atsScore: 4,
    atsNote: 'European CV convention includes a photo in a sidebar.',
    industries: ['general', 'business', 'technology'],
    experienceLevels: ['entry', 'mid', 'senior'],
    countries: ['EU', 'DE', 'FR', 'ES', 'IT'],
    recommendedFor: ['europe', 'eu cv', 'international'],
    tags: ['international', 'europe', 'photo', 'two-column'],
    popularity: 69,
    version: 1,
    description: 'A European-style CV with a sidebar photo and skills column.',
  },
  {
    id: 'us-professional',
    name: 'US Professional (no photo)',
    category: 'international',
    engine: 'swiss-single',
    settings: { accentColor: 'blue', spacing: { section: 3, item: 2, lineHeight: 3 } },
    photoSupport: 'none',
    atsScore: 5,
    atsNote: 'US convention omits photos; single column parses everywhere.',
    industries: ['general', 'business', 'technology'],
    experienceLevels: ['entry', 'mid', 'senior'],
    countries: ['US'],
    recommendedFor: ['us', 'north america', 'no photo'],
    tags: ['international', 'us', 'no-photo', 'single-column'],
    popularity: 90,
    version: 1,
    description: 'A US-standard, photo-free single-column resume.',
  },
];

// ---------------------------------------------------------------------------
// Lookups + composition
// ---------------------------------------------------------------------------

const BY_ID = new Map(RESUME_TEMPLATES.map((t) => [t.id, t]));

export function getTemplateById(id: string): ResumeTemplate | undefined {
  return BY_ID.get(id);
}

/**
 * Compose a catalog template into full {@link TemplateSettings}, layering its
 * preset over the engine defaults (via {@link applyTemplatePreset}, which seeds
 * signature fonts for single-typeface engines). This is what feeds the shared
 * renderer + the PDF export query params - one settings shape, no duplication.
 */
export function templateToSettings(
  template: ResumeTemplate,
  base: TemplateSettings = DEFAULT_TEMPLATE_SETTINGS
): TemplateSettings {
  const withEngine = applyTemplatePreset(base, template.engine);
  const p = template.settings;
  return {
    ...withEngine,
    ...p,
    template: template.engine,
    margins: { ...withEngine.margins, ...(p.margins ?? {}) },
    spacing: { ...withEngine.spacing, ...(p.spacing ?? {}) },
    fontSize: { ...withEngine.fontSize, ...(p.fontSize ?? {}) },
    showContactIcons: p.showContactIcons ?? withEngine.showContactIcons,
  };
}

/** Whether a template's declared photo support is consistent with its engine. */
export function photoSupportIsConsistent(template: ResumeTemplate): boolean {
  const cap = photoCapability(template.engine);
  // A template may never *require/support* a photo on a photo-incapable engine.
  if (!cap.supportsPhoto) return template.photoSupport === 'none';
  return true;
}

export const TEMPLATE_CATEGORIES: { id: TemplateCategory; label: string }[] = [
  { id: 'ats', label: 'ATS Optimized' },
  { id: 'professional', label: 'Professional' },
  { id: 'technology', label: 'Technology' },
  { id: 'creative', label: 'Creative' },
  { id: 'academic', label: 'Academic' },
  { id: 'career-stage', label: 'Career Stage' },
  { id: 'international', label: 'International' },
];

// ---------------------------------------------------------------------------
// Search / filter / sort (pure)
// ---------------------------------------------------------------------------

export interface TemplateFilter {
  query?: string;
  category?: TemplateCategory | 'all';
  photo?: 'all' | 'with-photo' | 'no-photo';
  minAts?: number;
  experienceLevel?: ExperienceLevel;
  country?: string;
}

export type TemplateSort = 'recommended' | 'popular' | 'ats' | 'name';

function matchesQuery(t: ResumeTemplate, q: string): boolean {
  const hay = [
    t.name,
    t.category,
    t.description,
    ...t.industries,
    ...t.recommendedFor,
    ...t.tags,
    ...t.countries,
  ]
    .join(' ')
    .toLowerCase();
  return q
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((term) => hay.includes(term));
}

export function filterTemplates(
  templates: ResumeTemplate[],
  filter: TemplateFilter
): ResumeTemplate[] {
  return templates.filter((t) => {
    if (filter.query && !matchesQuery(t, filter.query)) return false;
    if (filter.category && filter.category !== 'all' && t.category !== filter.category) {
      return false;
    }
    if (filter.photo === 'with-photo' && t.photoSupport === 'none') return false;
    if (filter.photo === 'no-photo' && t.photoSupport !== 'none') return false;
    if (typeof filter.minAts === 'number' && t.atsScore < filter.minAts) return false;
    if (filter.experienceLevel && !t.experienceLevels.includes(filter.experienceLevel)) {
      return false;
    }
    if (filter.country && !t.countries.includes(filter.country)) return false;
    return true;
  });
}

export function sortTemplates(templates: ResumeTemplate[], sort: TemplateSort): ResumeTemplate[] {
  const copy = [...templates];
  switch (sort) {
    case 'popular':
      return copy.sort((a, b) => b.popularity - a.popularity || a.name.localeCompare(b.name));
    case 'ats':
      return copy.sort((a, b) => b.atsScore - a.atsScore || b.popularity - a.popularity);
    case 'name':
      return copy.sort((a, b) => a.name.localeCompare(b.name));
    case 'recommended':
    default:
      // Default editorial ordering blends ATS confidence + popularity.
      return copy.sort((a, b) => b.atsScore * 20 + b.popularity - (a.atsScore * 20 + a.popularity));
  }
}
