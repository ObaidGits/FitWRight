import { LanguageProvider } from '@/lib/context/language-context';
import { ResumePreviewProvider } from '@/components/common/resume_previewer_context';
import { ResumeBuilder } from '@/components/builder/resume-builder';

/**
 * The advanced editor, now inside the authenticated app group so it renders with
 * the sidebar on desktop and the bottom nav on mobile.
 *
 * It previously lived in its own `(default)` group, which mounted no navigation
 * at all: the single way out was a "Back to dashboard" button pointing at
 * `/dashboard`, a route this app does not have. So the flagship editor was a
 * navigational dead end - and on a phone, with no bottom nav either, there was
 * genuinely no way to leave it.
 *
 * The two contexts the builder consumes are mounted here rather than in the
 * group layout, so the other pages in the group carry no extra providers.
 * `useResumePreview` throws when unmounted, and `useTranslations` silently
 * degrades to English, so both must be present for this route specifically.
 */
export default function BuilderPage() {
  return (
    <LanguageProvider>
      <ResumePreviewProvider>
        <ResumeBuilder />
      </ResumePreviewProvider>
    </LanguageProvider>
  );
}
