'use client';

import { useMemo } from 'react';
import { type ResumeData } from '@/components/dashboard/resume-component';
import { segmentTextByKeywords } from '@/lib/utils/keyword-matcher';
import { FileUser, Briefcase, GraduationCap, FolderKanban, Wrench } from 'lucide-react';
import { useTranslations } from '@/lib/i18n';

interface HighlightedResumeViewProps {
  resumeData: ResumeData;
  keywords: Set<string>;
}

/**
 * Display resume content with matching keywords highlighted.
 * Shows all resume sections with visual highlighting of JD matches.
 */
export function HighlightedResumeView({ resumeData, keywords }: HighlightedResumeViewProps) {
  const { t } = useTranslations();

  // Drop blank/whitespace-only entries so empty lines (e.g. from editing in the
  // builder) never render in the preview (issue #763).
  const visibleTechnicalSkills =
    resumeData.additional?.technicalSkills?.filter(
      (item): item is string => typeof item === 'string' && item.trim() !== ''
    ) ?? [];
  const visibleLanguages =
    resumeData.additional?.languages?.filter(
      (item): item is string => typeof item === 'string' && item.trim() !== ''
    ) ?? [];
  const visibleCertificationsTraining =
    resumeData.additional?.certificationsTraining?.filter(
      (item): item is string => typeof item === 'string' && item.trim() !== ''
    ) ?? [];

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-[var(--border)] bg-[var(--secondary)]/40 p-4">
        <FileUser className="h-4 w-4 text-[var(--muted-foreground)]" />
        <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
          {t('builder.jdMatch.yourResume')}
        </h3>
        <span className="ml-2 text-xs text-[var(--muted-foreground)]">
          {t('builder.jdMatch.matchingKeywordsHighlighted')}
        </span>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-6">
        {/* Summary */}
        {resumeData.summary && (
          <Section title={t('resume.sections.summary')} icon={<FileUser className="w-4 h-4" />}>
            <HighlightedText text={resumeData.summary} keywords={keywords} />
          </Section>
        )}

        {/* Work Experience */}
        {resumeData.workExperience && resumeData.workExperience.length > 0 && (
          <Section title={t('resume.sections.experience')} icon={<Briefcase className="w-4 h-4" />}>
            {resumeData.workExperience.map((exp) => (
              <div key={exp.id} className="mb-4 last:mb-0">
                <div className="font-semibold text-[var(--foreground)]">
                  <HighlightedText text={exp.title || ''} keywords={keywords} />
                  {exp.company && (
                    <span className="text-[var(--foreground)]">
                      {t('builder.jdMatch.atSeparator')}
                      <HighlightedText text={exp.company} keywords={keywords} />
                    </span>
                  )}
                </div>
                {exp.years && (
                  <div className="text-xs text-[var(--muted-foreground)] mb-1">{exp.years}</div>
                )}
                {exp.description && (
                  <ul className="list-disc list-inside space-y-1 text-sm">
                    {exp.description.map((bullet, i) => (
                      <li key={i} className="text-[var(--foreground)]">
                        <HighlightedText text={bullet} keywords={keywords} />
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </Section>
        )}

        {/* Education */}
        {resumeData.education && resumeData.education.length > 0 && (
          <Section
            title={t('resume.sections.education')}
            icon={<GraduationCap className="w-4 h-4" />}
          >
            {resumeData.education.map((edu) => (
              <div key={edu.id} className="mb-3 last:mb-0">
                <div className="font-semibold text-[var(--foreground)]">
                  <HighlightedText text={edu.degree || ''} keywords={keywords} />
                </div>
                {edu.institution && (
                  <div className="text-sm text-[var(--foreground)]">
                    <HighlightedText text={edu.institution} keywords={keywords} />
                  </div>
                )}
                {edu.years && (
                  <div className="text-xs text-[var(--muted-foreground)]">{edu.years}</div>
                )}
              </div>
            ))}
          </Section>
        )}

        {/* Projects */}
        {resumeData.personalProjects && resumeData.personalProjects.length > 0 && (
          <Section
            title={t('resume.sections.projects')}
            icon={<FolderKanban className="w-4 h-4" />}
          >
            {resumeData.personalProjects.map((proj) => (
              <div key={proj.id} className="mb-4 last:mb-0">
                <div className="font-semibold text-[var(--foreground)]">
                  <HighlightedText text={proj.name || ''} keywords={keywords} />
                  {proj.role && (
                    <span className="text-[var(--foreground)] font-normal">
                      {' '}
                      {t('builder.jdMatch.roleSeparator')}{' '}
                      <HighlightedText text={proj.role} keywords={keywords} />
                    </span>
                  )}
                </div>
                {proj.years && (
                  <div className="text-xs text-[var(--muted-foreground)] mb-1">{proj.years}</div>
                )}
                {proj.description && (
                  <ul className="list-disc list-inside space-y-1 text-sm">
                    {proj.description.map((bullet, i) => (
                      <li key={i} className="text-[var(--foreground)]">
                        <HighlightedText text={bullet} keywords={keywords} />
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </Section>
        )}

        {/* Skills */}
        {resumeData.additional && (
          <Section title={t('resume.sections.skills')} icon={<Wrench className="w-4 h-4" />}>
            {visibleTechnicalSkills.length > 0 && (
              <div className="mb-3">
                <div className="mb-1 text-xs uppercase text-[var(--muted-foreground)]">
                  {t('resume.additional.technicalSkills')}
                </div>
                <div className="flex flex-wrap gap-1">
                  {visibleTechnicalSkills.map((skill, i) => (
                    <SkillTag key={i} text={skill} keywords={keywords} />
                  ))}
                </div>
              </div>
            )}

            {visibleLanguages.length > 0 && (
              <div className="mb-3">
                <div className="mb-1 text-xs uppercase text-[var(--muted-foreground)]">
                  {t('resume.sections.languages')}
                </div>
                <div className="flex flex-wrap gap-1">
                  {visibleLanguages.map((lang, i) => (
                    <SkillTag key={i} text={lang} keywords={keywords} />
                  ))}
                </div>
              </div>
            )}

            {visibleCertificationsTraining.length > 0 && (
              <div className="mb-3">
                <div className="mb-1 text-xs uppercase text-[var(--muted-foreground)]">
                  {t('resume.sections.certifications')}
                </div>
                <ul className="list-disc list-inside space-y-1 text-sm">
                  {visibleCertificationsTraining.map((cert, i) => (
                    <li key={i} className="text-[var(--foreground)]">
                      <HighlightedText text={cert} keywords={keywords} />
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Section>
        )}
      </div>
    </div>
  );
}

/**
 * Section wrapper component
 */
function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)]">
      <div className="flex items-center gap-2 border-b border-[var(--border)] bg-[var(--secondary)]/40 px-3 py-2">
        {icon}
        <span className="text-xs font-semibold uppercase text-[var(--muted-foreground)]">
          {title}
        </span>
      </div>
      <div className="p-3">{children}</div>
    </div>
  );
}

/**
 * Component to render text with highlighted keywords.
 */
function HighlightedText({ text, keywords }: { text: string; keywords: Set<string> }) {
  const segments = useMemo(() => segmentTextByKeywords(text, keywords), [text, keywords]);

  return (
    <span>
      {segments.map((segment, i) =>
        segment.isMatch ? (
          <mark
            key={i}
            className="rounded-[var(--radius-at-sm)] bg-[var(--at-warning)]/25 px-0.5 text-[var(--foreground)]"
          >
            {segment.text}
          </mark>
        ) : (
          <span key={i}>{segment.text}</span>
        )
      )}
    </span>
  );
}

/**
 * Skill tag with optional highlighting
 */
function SkillTag({ text, keywords }: { text: string; keywords: Set<string> }) {
  const isMatch = keywords.has(text.toLowerCase());

  return (
    <span
      className={`inline-block rounded-[var(--radius-at-sm)] px-2 py-0.5 text-xs ${
        isMatch
          ? 'bg-[var(--at-warning)]/25 font-medium text-[var(--foreground)]'
          : 'bg-[var(--secondary)] text-[var(--muted-foreground)]'
      }`}
    >
      {text}
    </span>
  );
}
