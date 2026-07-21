/**
 * Landing FAQ content - shared between the accessible on-page accordion
 * (`faq.tsx`) and the FAQPage JSON-LD emitted on the landing page. Keeping a
 * single source guarantees the structured data always matches what users see
 * (a Google rich-results requirement).
 */
export const LANDING_FAQS: ReadonlyArray<{ q: string; a: string }> = [
  {
    q: 'Why use FitWright?',
    a: 'A single master resume rarely fits every role. FitWright reshapes yours for each job description - highlighting the relevant experience and keywords - so you spend minutes, not hours, per application.',
  },
  {
    q: 'Which AI providers are supported?',
    a: 'OpenAI, Anthropic, Google Gemini, OpenRouter, DeepSeek, Groq, and any OpenAI-compatible server. You can also run fully local models with Ollama - no cloud, no cost.',
  },
  {
    q: 'Do you store my data or API key?',
    a: 'You bring your own API key, and it is encrypted at rest on your own instance. Your resume content stays in your local database. FitWright never sends your data to a third party beyond the AI provider you choose.',
  },
  {
    q: 'Can I export polished PDFs?',
    a: 'Yes. Every resume and cover letter exports to a clean, ATS-friendly PDF using multiple templates with a live preview that matches the output exactly.',
  },
  {
    q: 'Can I edit what the AI produces?',
    a: 'Always. Every AI change is shown as a preview with a clear diff - you accept, tweak, or discard. You can also ask AI to rewrite any single bullet, and edit everything by hand.',
  },
  {
    q: 'Does it handle multiple resumes and applications?',
    a: 'Yes. Keep one master resume, generate a tailored variant per job, and track every application from applied to offer on a Kanban board.',
  },
  {
    q: 'Is FitWright open source?',
    a: 'Yes - the full source is on GitHub. You can self-host it, inspect exactly how your data is handled, and contribute.',
  },
];
