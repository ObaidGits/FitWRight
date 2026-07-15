/**
 * Cross-flow "preferred template" bridge.
 *
 * The template gallery persists the user's chosen template id here so the very
 * next resume flow (wizard, create) opens already rendered in that template —
 * without threading query params through every route. Reads are fully guarded
 * so this is a no-op (returns defaults) on the server or in tests.
 */
import { type TemplateSettings, DEFAULT_TEMPLATE_SETTINGS } from '@/lib/types/template-settings';
import { getTemplateById, templateToSettings } from '@/lib/resume/template-catalog';

const PREFERRED_KEY = 'fitwright:preferred-template';

export function setPreferredTemplateId(id: string): void {
  try {
    localStorage.setItem(PREFERRED_KEY, id);
  } catch {
    /* ignore (SSR / disabled storage) */
  }
}

export function getPreferredTemplateId(): string | null {
  try {
    return localStorage.getItem(PREFERRED_KEY);
  } catch {
    return null;
  }
}

/**
 * Resolve the preferred template into full {@link TemplateSettings}, falling
 * back to the defaults when nothing is stored or the id is unknown.
 */
export function getPreferredTemplateSettings(
  base: TemplateSettings = DEFAULT_TEMPLATE_SETTINGS
): TemplateSettings {
  const id = getPreferredTemplateId();
  if (!id) return base;
  const template = getTemplateById(id);
  return template ? templateToSettings(template, base) : base;
}
