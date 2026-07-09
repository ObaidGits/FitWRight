import type { Metadata } from 'next';

export const metadata: Metadata = { title: 'Terms of Use — FitWright' };

export default function TermsPage() {
  return (
    <article className="mx-auto w-full max-w-3xl px-4 py-16 md:px-8">
      <h1 className="text-3xl font-semibold">Terms of Use</h1>
      <p className="mt-2 text-sm text-[var(--muted-foreground)]">
        Last updated {new Date().getFullYear()}
      </p>
      <div className="mt-8 space-y-6 text-[var(--foreground)]">
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">Use of the service</h2>
          <p className="text-[var(--muted-foreground)]">
            FitWright is provided as-is to help you tailor resumes and manage applications. You are
            responsible for the accuracy of the content you submit and generate.
          </p>
        </section>
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">Honesty</h2>
          <p className="text-[var(--muted-foreground)]">
            FitWright is designed to tailor your real experience, not invent it. You are responsible
            for ensuring your resume remains truthful.
          </p>
        </section>
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">License</h2>
          <p className="text-[var(--muted-foreground)]">
            FitWright is released under the Apache License 2.0.
          </p>
        </section>
      </div>
    </article>
  );
}
