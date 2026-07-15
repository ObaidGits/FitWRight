/**
 * Public profile view (P7) — the rendered share page.
 *
 * A server-safe, presentational projection of the public profile: hero,
 * summary, experience timeline, projects, skills, education, and contact/social
 * links. It renders ONLY the fields the public projection exposes (never private
 * data). Themes are token-driven so they stay consistent with FitWright's design
 * language and support dark mode + responsive layouts out of the box.
 */
import Link from 'next/link';
import Github from 'lucide-react/dist/esm/icons/github';
import Linkedin from 'lucide-react/dist/esm/icons/linkedin';
import Globe from 'lucide-react/dist/esm/icons/globe';
import Download from 'lucide-react/dist/esm/icons/download';
import MapPin from 'lucide-react/dist/esm/icons/map-pin';

import type { PublicProfile } from '@/lib/api/professional-profile';
import { ProfileAvatar } from '@/components/common/profile-avatar';

const THEME_CLASSES: Record<string, { root: string; hero: string; heading: string }> = {
  minimal: { root: '', hero: '', heading: '' },
  modern: {
    root: '',
    hero: 'bg-gradient-to-br from-[var(--primary)]/10 via-transparent to-[var(--at-ai-surface,transparent)]',
    heading: 'text-4xl',
  },
  developer: {
    root: 'font-mono',
    hero: 'bg-[var(--at-surface-2,var(--secondary))]',
    heading: 'tracking-tight',
  },
};

export function PublicProfileView({
  profile,
  vcardUrl,
  theme = 'minimal',
}: {
  profile: PublicProfile;
  vcardUrl: string;
  theme?: 'minimal' | 'modern' | 'developer';
}) {
  const id = profile.identity;
  const t = THEME_CLASSES[theme] ?? THEME_CLASSES.minimal;
  const links = [
    id.website && { href: id.website, icon: Globe, label: 'Website' },
    id.linkedin && { href: id.linkedin, icon: Linkedin, label: 'LinkedIn' },
    id.github && { href: id.github, icon: Github, label: 'GitHub' },
  ].filter(Boolean) as { href: string; icon: typeof Globe; label: string }[];

  return (
    <div
      className={`min-h-dvh bg-[var(--background)] text-[var(--foreground)] ${t.root}`}
      data-theme={theme}
    >
      {/* Ambient hero */}
      <div className={`relative overflow-hidden border-b border-[var(--border)] ${t.hero}`}>
        <div className="at-blob pointer-events-none absolute -left-24 -top-24 h-72 w-72 opacity-40" />
        <div className="mx-auto max-w-3xl px-5 py-14 sm:py-20">
          <div className="flex flex-col items-start gap-5 sm:flex-row sm:items-center">
            <ProfileAvatar
              url={id.avatarUrl}
              srcset={id.avatarSrcset}
              size={80}
              name={id.name}
              dominantColor={id.avatarDominantColor}
              priority
              className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-full bg-[var(--primary)]/12 text-2xl font-semibold text-[var(--primary)]"
            />
            <div className="min-w-0">
              <h1 className={`text-3xl font-semibold tracking-tight ${t.heading}`}>{id.name}</h1>
              {id.headline && (
                <p className="mt-1 text-lg text-[var(--muted-foreground)]">{id.headline}</p>
              )}
              {id.location && (
                <p className="mt-1 flex items-center gap-1 text-sm text-[var(--muted-foreground)]">
                  <MapPin className="h-3.5 w-3.5" /> {id.location}
                </p>
              )}
            </div>
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-2">
            {links.map((l) => {
              const Icon = l.icon;
              return (
                <a
                  key={l.label}
                  href={l.href}
                  target="_blank"
                  rel="noopener noreferrer nofollow"
                  className="inline-flex items-center gap-1.5 rounded-[var(--radius-at-md)] border border-[var(--border)] px-3 py-1.5 text-sm transition-colors hover:bg-[var(--accent)]"
                >
                  <Icon className="h-4 w-4" /> {l.label}
                </a>
              );
            })}
            <a
              href={vcardUrl}
              className="inline-flex items-center gap-1.5 rounded-[var(--radius-at-md)] bg-[var(--primary)] px-3 py-1.5 text-sm font-medium text-[var(--primary-foreground)] transition hover:brightness-110"
            >
              <Download className="h-4 w-4" /> Save contact
            </a>
          </div>
        </div>
      </div>

      <main className="mx-auto max-w-3xl space-y-12 px-5 py-12">
        {profile.summary && (
          <section aria-labelledby="about-heading">
            <h2
              id="about-heading"
              className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--muted-foreground)]"
            >
              About
            </h2>
            <p className="text-base leading-relaxed">{profile.summary}</p>
          </section>
        )}

        {profile.experience.length > 0 && (
          <section aria-labelledby="exp-heading">
            <h2
              id="exp-heading"
              className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--muted-foreground)]"
            >
              Experience
            </h2>
            <ol className="space-y-6 border-l border-[var(--border)] pl-5">
              {profile.experience.map((e, i) => (
                <li key={i} className="relative">
                  <span className="absolute -left-[23px] top-1.5 h-2.5 w-2.5 rounded-full bg-[var(--primary)]" />
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <h3 className="font-medium">
                      {e.title}
                      {e.company && (
                        <span className="text-[var(--muted-foreground)]"> · {e.company}</span>
                      )}
                    </h3>
                    {e.years && (
                      <span className="text-xs text-[var(--muted-foreground)]">{e.years}</span>
                    )}
                  </div>
                  {e.description.length > 0 && (
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-[var(--muted-foreground)]">
                      {e.description.map((d, j) => (
                        <li key={j}>{d}</li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ol>
          </section>
        )}

        {profile.projects.length > 0 && (
          <section aria-labelledby="proj-heading">
            <h2
              id="proj-heading"
              className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--muted-foreground)]"
            >
              Projects
            </h2>
            <div className="grid gap-4 sm:grid-cols-2">
              {profile.projects.map((p, i) => (
                <div
                  key={i}
                  className="rounded-[var(--radius-at-lg)] border border-[var(--border)] p-4"
                >
                  <h3 className="font-medium">{p.name}</h3>
                  {p.description.length > 0 && (
                    <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                      {p.description[0]}
                    </p>
                  )}
                  {p.tech.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {p.tech.slice(0, 6).map((t) => (
                        <span
                          key={t}
                          className="rounded-full bg-[var(--secondary)] px-2 py-0.5 text-xs"
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="mt-2 flex gap-3 text-sm">
                    {p.github && (
                      <a
                        href={p.github}
                        target="_blank"
                        rel="noopener noreferrer nofollow"
                        className="text-[var(--primary)] hover:underline"
                      >
                        Code
                      </a>
                    )}
                    {p.website && (
                      <a
                        href={p.website}
                        target="_blank"
                        rel="noopener noreferrer nofollow"
                        className="text-[var(--primary)] hover:underline"
                      >
                        Live
                      </a>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {profile.skills.length > 0 && (
          <section aria-labelledby="skills-heading">
            <h2
              id="skills-heading"
              className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--muted-foreground)]"
            >
              Skills
            </h2>
            <div className="flex flex-wrap gap-1.5">
              {profile.skills.map((s) => (
                <span key={s} className="rounded-full bg-[var(--secondary)] px-2.5 py-1 text-sm">
                  {s}
                </span>
              ))}
            </div>
          </section>
        )}

        {profile.education.length > 0 && (
          <section aria-labelledby="edu-heading">
            <h2
              id="edu-heading"
              className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--muted-foreground)]"
            >
              Education
            </h2>
            <ul className="space-y-2">
              {profile.education.map((e, i) => (
                <li key={i} className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="font-medium">
                    {e.degree}
                    {e.institution && (
                      <span className="text-[var(--muted-foreground)]"> · {e.institution}</span>
                    )}
                  </span>
                  {e.years && (
                    <span className="text-xs text-[var(--muted-foreground)]">{e.years}</span>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>

      <footer className="border-t border-[var(--border)] py-8 text-center text-sm text-[var(--muted-foreground)]">
        <Link href="/" className="hover:text-[var(--foreground)]">
          Built with FitWright
        </Link>
      </footer>
    </div>
  );
}
