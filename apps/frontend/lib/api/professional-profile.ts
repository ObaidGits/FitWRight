/**
 * Professional Profile System API client
 * (docs/architecture/PROFILE_SYSTEM_PLAN.md).
 *
 * The profile is the user's single canonical career document; resumes are
 * generated snapshots produced from it by the backend Projection Engine. This
 * module mirrors the backend ``ProfileData`` schema and the ``/profile`` routes.
 * Distinct from ``lib/api/profile.ts`` (the lightweight account headline/avatar).
 */
import { API_BASE, apiFetch, apiPatch, apiPost, apiPut } from './client';
import type { TemplateSettings } from '@/lib/types/template-settings';

// --- Domain types (mirror app/profile/schemas.py) --------------------------

export interface ProfileIdentity {
  name: string;
  headline: string;
  currentRole: string;
  currentCompany: string;
  yearsExperience: number | null;
  industry: string;
  careerStage: string;
  targetRoles: string[];
  careerObjective: string;
  employmentStatus: string;
  availability: string;
  remotePreference: string;
  relocation: boolean | null;
  noticePeriod: string;
  workAuthorization: string;
  visaStatus: string;
  preferredLocations: string[];
  salaryExpectation: string;
  careerVisibility: 'private' | 'unlisted' | 'public';
  email: string;
  phone: string;
  location: string;
  timezone: string;
  website: string | null;
  linkedin: string | null;
  github: string | null;
  avatarUrl: string | null;
}

export interface ProfileExperience {
  uid: string;
  title: string;
  company: string;
  location: string | null;
  years: string;
  current: boolean;
  description: string[];
  tech: string[];
}

export interface ProfileEducation {
  uid: string;
  institution: string;
  degree: string;
  years: string;
  description: string | null;
}

export interface ProfileProject {
  uid: string;
  name: string;
  role: string;
  years: string;
  github: string | null;
  website: string | null;
  description: string[];
  tech: string[];
  experienceUid: string | null;
}

export interface Skill {
  uid: string;
  canonical: string;
  displayName: string;
  aliases: string[];
  category: string;
  subcategory: string;
  yearsExperience: number | null;
  proficiency: string;
  lastUsed: string;
  confidence: number | null;
  verificationSource: string;
  aiNormalizedName: string;
  evidenceUids: string[];
}

export interface ProfileSkills {
  technical: Skill[];
  soft: Skill[];
  languages: Skill[];
  tools: Skill[];
}

export interface Certification {
  uid: string;
  name: string;
  issuer: string;
  date: string;
  url: string | null;
}

export interface Achievement {
  uid: string;
  kind: string;
  title: string;
  description: string | null;
  date: string;
  url: string | null;
  relatedUid: string | null;
}

export interface ProfileLinkItem {
  uid: string;
  label: string;
  url: string;
  kind: string;
}

export interface AiMemory {
  writingStyle: string;
  tone: string;
  atsPreference: string;
  templatePreference: string;
  targetCompanies: string[];
  targetIndustries: string[];
  dos: string[];
  donts: string[];
}

export interface ProfileMeta {
  schemaVersion: number;
  source: string;
  lastImportedResumeId: string | null;
  provenance: Record<string, unknown>;
}

export interface ProfileData {
  identity: ProfileIdentity;
  summary: string;
  workExperience: ProfileExperience[];
  education: ProfileEducation[];
  personalProjects: ProfileProject[];
  skills: ProfileSkills;
  certifications: Certification[];
  achievements: Achievement[];
  interests: string[];
  links: ProfileLinkItem[];
  customSections: Record<string, unknown>;
  sectionMeta: unknown[];
  aiMemory: AiMemory;
  meta: ProfileMeta;
}

export interface ProfileResponse {
  data: ProfileData;
  completeness: number;
  version: number;
  updated_at: string | null;
}

export interface CompletenessSuggestion {
  key: string;
  label: string;
  weight: number;
  done: boolean;
}

export interface ProfileCompletenessResponse {
  score: number;
  suggestions: CompletenessSuggestion[];
}

export interface GenerateResumeResponse {
  resume_data: Record<string, unknown>;
  resume_id: string | null;
}

export interface ProfileVersionMeta {
  id: string;
  profile_id: string;
  source: string;
  label: string | null;
  content_hash: string;
  size_bytes: number;
  created_at: string;
}

export interface ProfileVersionListResponse {
  items: ProfileVersionMeta[];
  next_cursor: string | null;
}

export interface ProfileVersionData extends ProfileVersionMeta {
  data: ProfileData;
}

/** A version-CAS conflict surfaced by PATCH (409). */
export class ProfileConflictError extends Error {
  currentVersion: number | null;
  current: ProfileResponse | null;
  constructor(currentVersion: number | null, current: ProfileResponse | null) {
    super('Profile was modified elsewhere.');
    this.name = 'ProfileConflictError';
    this.currentVersion = currentVersion;
    this.current = current;
  }
}

async function asJson<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as {
      detail?: unknown;
      error?: { message?: string };
    };
    const detail = typeof data.detail === 'string' ? data.detail : (data.error?.message ?? null);
    throw new Error(detail || `${fallback} (status ${res.status}).`);
  }
  return res.json() as Promise<T>;
}

// --- Endpoints -------------------------------------------------------------

export async function getProfessionalProfile(): Promise<ProfileResponse> {
  return asJson<ProfileResponse>(
    await apiFetch('/profile', { credentials: 'include' }),
    'Failed to load profile'
  );
}

export async function updateProfessionalProfile(
  data: ProfileData,
  baseVersion: number
): Promise<ProfileResponse> {
  const res = await apiPatch('/profile', { data, base_version: baseVersion });
  if (res.status === 409) {
    const body = (await res.json().catch(() => ({}))) as {
      detail?: {
        current_version?: number | null;
        current?: ProfileResponse | null;
      };
    };
    throw new ProfileConflictError(
      body.detail?.current_version ?? null,
      body.detail?.current ?? null
    );
  }
  return asJson<ProfileResponse>(res, 'Failed to save profile');
}

export async function getProfileCompleteness(): Promise<ProfileCompletenessResponse> {
  return asJson<ProfileCompletenessResponse>(
    await apiFetch('/profile/completeness', { credentials: 'include' }),
    'Failed to load completeness'
  );
}

export async function generateResumeFromProfile(options: {
  title?: string | null;
  persist?: boolean;
  as_master?: boolean;
  include_photo?: boolean;
  /** Persisted appearance (TemplateSettings) so the resume opens in the chosen template. */
  template_settings?: TemplateSettings | null;
}): Promise<GenerateResumeResponse> {
  return asJson<GenerateResumeResponse>(
    await apiPost('/profile/generate-resume', options),
    'Failed to generate resume'
  );
}

export async function listProfileVersions(cursor?: string): Promise<ProfileVersionListResponse> {
  const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
  return asJson<ProfileVersionListResponse>(
    await apiFetch(`/profile/versions${qs}`, { credentials: 'include' }),
    'Failed to load history'
  );
}

export async function getProfileVersion(versionId: string): Promise<ProfileVersionData> {
  return asJson<ProfileVersionData>(
    await apiFetch(`/profile/versions/${versionId}`, { credentials: 'include' }),
    'Failed to load version'
  );
}

export async function restoreProfileVersion(versionId: string): Promise<ProfileResponse> {
  return asJson<ProfileResponse>(
    await apiPost(`/profile/versions/${versionId}/restore`, {}),
    'Failed to restore version'
  );
}

// --- Import / Merge (P3) ---------------------------------------------------

export interface FieldChange {
  field: string;
  existing: unknown;
  incoming: unknown;
}

export interface MergeOperation {
  id: string;
  section: string;
  op: 'add' | 'update' | 'duplicate' | 'conflict';
  label: string;
  confidence: number;
  similarity: number | null;
  existing_uid: string | null;
  existing: unknown;
  incoming: unknown;
  changes: FieldChange[];
  default_resolution: string;
  allowed_resolutions: string[];
}

export interface MergePlan {
  operations: MergeOperation[];
  counts: Record<string, number>;
}

export interface ImportStatistics {
  quality_score: number;
  sections: Record<string, number>;
  total_operations: number;
  new_items: number;
  updates: number;
  conflicts: number;
  duplicates: number;
}

export interface ImportPreviewResponse {
  source: string;
  incoming: ProfileData;
  plan: MergePlan;
  statistics: ImportStatistics;
  warnings: string[];
}

export interface ApplyMergeResponse {
  data: ProfileData;
  completeness: number;
  version: number;
  applied: number;
  skipped: number;
}

export async function previewImport(
  source: string,
  payload: Record<string, unknown>
): Promise<ImportPreviewResponse> {
  return asJson<ImportPreviewResponse>(
    await apiPost('/profile/import/preview', { source, payload }),
    'Failed to preview import'
  );
}

export async function applyImport(args: {
  incoming: ProfileData;
  resolutions: Record<string, string>;
  base_version: number;
  source?: 'import' | 'merge';
}): Promise<ApplyMergeResponse> {
  return asJson<ApplyMergeResponse>(
    await apiPost('/profile/import/apply', { source: 'import', ...args }),
    'Failed to apply import'
  );
}

// --- Synchronization (P4) --------------------------------------------------

export interface SyncChange {
  path: string;
  action: 'added' | 'removed' | 'changed';
  before: unknown;
  after: unknown;
}

export interface SyncPreviewResponse {
  resume_id: string;
  resume_version: number;
  changes: SyncChange[];
  projected: Record<string, unknown>;
  immutable: boolean;
  reason: string | null;
}

export async function previewSync(
  resumeId: string,
  includePhoto = false
): Promise<SyncPreviewResponse> {
  const qs = includePhoto ? '?include_photo=true' : '';
  return asJson<SyncPreviewResponse>(
    await apiFetch(`/profile/sync/${resumeId}${qs}`, { credentials: 'include' }),
    'Failed to preview sync'
  );
}

export async function applySync(
  resumeId: string,
  baseVersion: number,
  includePhoto = false
): Promise<{ resume_id: string; resume: Record<string, unknown> | null }> {
  return asJson(
    await apiPost(`/profile/sync/${resumeId}`, {
      base_version: baseVersion,
      include_photo: includePhoto,
    }),
    'Failed to sync resume'
  );
}

// --- AI layer (P5) ---------------------------------------------------------

export async function updateAiMemory(
  aiMemory: AiMemory,
  baseVersion: number
): Promise<ProfileResponse> {
  const res = await apiPut('/profile/ai-memory', {
    aiMemory,
    base_version: baseVersion,
  });
  return asJson<ProfileResponse>(res, 'Failed to save AI memory');
}

export interface AiSuggestResponse {
  kind: string;
  suggestion: unknown;
  note: string | null;
}

export async function aiSuggest(
  kind: 'summary' | 'experience_bullets' | 'skills_normalize',
  experienceUid?: string
): Promise<AiSuggestResponse> {
  return asJson<AiSuggestResponse>(
    await apiPost('/profile/ai/suggest', { kind, experience_uid: experienceUid ?? null }),
    'Failed to get suggestion'
  );
}

export interface SkillSuggestion {
  canonical: string;
  displayName: string;
  category: string;
}

export async function suggestSkills(q: string): Promise<SkillSuggestion[]> {
  const body = await asJson<{ suggestions: SkillSuggestion[] }>(
    await apiFetch(`/profile/skills/suggest?q=${encodeURIComponent(q)}`, {
      credentials: 'include',
    }),
    'Failed to load skill suggestions'
  );
  return body.suggestions;
}

// --- Public projection platform (P6) ---------------------------------------

/** Fetch a projection (public/portfolio/json-resume) as a plain object. */
export async function getProjection(
  kind: 'public' | 'portfolio' | 'export/json-resume'
): Promise<Record<string, unknown>> {
  return asJson<Record<string, unknown>>(
    await apiFetch(`/profile/${kind}`, { credentials: 'include' }),
    'Failed to load projection'
  );
}

// --- Public sharing (P7) ---------------------------------------------------

export type Visibility = 'private' | 'unlisted' | 'public';
export type PublicTheme = 'minimal' | 'modern' | 'developer';

export interface PublicationState {
  public_slug: string | null;
  visibility: Visibility;
  public_theme: PublicTheme;
}

export interface PublicProfile {
  slug: string;
  visibility: Visibility;
  identity: {
    name?: string;
    headline?: string;
    location?: string;
    website?: string | null;
    linkedin?: string | null;
    github?: string | null;
    avatarUrl?: string | null;
    /** Responsive variants of the canonical master (Photo System). */
    avatarSrcset?: { url: string; width: number }[];
    /** Master metadata for CLS-free layout + placeholder. */
    avatarWidth?: number | null;
    avatarHeight?: number | null;
    avatarDominantColor?: string | null;
  };
  summary: string;
  experience: { title: string; company: string; years: string; description: string[] }[];
  projects: {
    name: string;
    role: string;
    github: string | null;
    website: string | null;
    description: string[];
    tech: string[];
  }[];
  skills: string[];
  education: { institution: string; degree: string; years: string }[];
}

export interface PublicProfilePage {
  profile: PublicProfile;
  json_ld: Record<string, unknown>;
  indexable: boolean;
  theme: PublicTheme;
}

export interface ProfileSearchResult {
  type: string;
  uid: string;
  section: string;
  title: string;
  subtitle: string;
  snippet: string;
  score: number;
}

export interface ProfileAnalytics {
  counters: Record<string, number>;
  completeness: number;
  total_events: number;
}

export async function getPublicationState(): Promise<PublicationState> {
  return asJson<PublicationState>(
    await apiFetch('/profile/publication', { credentials: 'include' }),
    'Failed to load publication state'
  );
}

export async function publishProfile(
  visibility: 'public' | 'unlisted',
  opts?: { slug?: string; theme?: PublicTheme }
): Promise<PublicationState> {
  return asJson<PublicationState>(
    await apiPost('/profile/publish', {
      visibility,
      slug: opts?.slug ?? null,
      theme: opts?.theme ?? null,
    }),
    'Failed to publish profile'
  );
}

export async function searchProfile(q: string): Promise<ProfileSearchResult[]> {
  const body = await asJson<{ query: string; results: ProfileSearchResult[] }>(
    await apiFetch(`/profile/search?q=${encodeURIComponent(q)}`, { credentials: 'include' }),
    'Search failed'
  );
  return body.results;
}

export async function getProfileAnalytics(): Promise<ProfileAnalytics> {
  return asJson<ProfileAnalytics>(
    await apiFetch('/profile/analytics', { credentials: 'include' }),
    'Failed to load analytics'
  );
}

export async function unpublishProfile(): Promise<PublicationState> {
  return asJson<PublicationState>(
    await apiPost('/profile/unpublish', {}),
    'Failed to unpublish profile'
  );
}

/**
 * Fetch a public profile page by slug (server- or client-side). Returns null on
 * 404 (private/unknown) so the caller can render notFound().
 */
export async function getPublicProfilePage(slug: string): Promise<PublicProfilePage | null> {
  const res = await apiFetch(`/public/profiles/${encodeURIComponent(slug)}`, {
    cache: 'no-store',
    skipAuthHandling: true,
  });
  if (res.status === 404) return null;
  return asJson<PublicProfilePage>(res, 'Failed to load public profile');
}

/** Absolute URL to the public vCard download for a slug. */
export function publicVcardUrl(slug: string): string {
  return `${API_BASE}/public/profiles/${encodeURIComponent(slug)}/vcard`;
}
