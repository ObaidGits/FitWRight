/**
 * Capability landing-page content - the single source of truth for FitWright's
 * feature landing pages (/resume-tailoring, /ats-resume-checker, etc.).
 *
 * Every field is truthful and grounded in a real, shipped capability (see the
 * home page feature list). Each capability owns a distinct keyword cluster
 * (lib/seo/page-keywords) so the pages never cannibalize one another or the
 * broad home page. Content is intentionally substantive (definition + steps +
 * outcomes + FAQ + cross-links) - never thin, never duplicated.
 *
 * Data is pure (no React) so it can be imported by server components, the
 * sitemap, and tests. Icons are referenced by key and mapped in the renderer.
 */

export type CapabilityIcon =
  | 'upload'
  | 'search'
  | 'sparkles'
  | 'pen'
  | 'gauge'
  | 'list'
  | 'eye'
  | 'badge'
  | 'mail'
  | 'message'
  | 'shield'
  | 'target'
  | 'brain'
  | 'file';

export interface CapabilityStep {
  icon: CapabilityIcon;
  title: string;
  body: string;
}

export interface CapabilityOutcome {
  icon: CapabilityIcon;
  title: string;
  body: string;
}

export interface Capability {
  /** Route slug, e.g. 'resume-tailoring' (no leading slash). */
  slug: string;
  eyebrow: string;
  h1: string;
  heroSub: string;
  /** Metadata. */
  metaTitle: string;
  metaDescription: string;
  socialTitle: string;
  /** Keyword cluster key in lib/seo/page-keywords KEYWORDS. */
  keywordGroup: 'tailoring' | 'atsChecker' | 'coverLetter' | 'interviewPrep';
  /** "What is ...?" definition block (entity-first, AI/LLM friendly). */
  definitionHeading: string;
  definition: string[];
  /** HowTo. */
  howToName: string;
  howToDescription: string;
  steps: CapabilityStep[];
  /** Outcomes. */
  outcomesHeading: string;
  outcomesSub: string;
  outcomes: CapabilityOutcome[];
  faqs: { q: string; a: string }[];
  /** CTA + cross-links (topic cluster). */
  ctaHeading: string;
  ctaSub: string;
  primaryCtaLabel: string;
}

/** Path helper for a capability. */
export function capabilityPath(slug: string): string {
  return `/${slug}`;
}

export const CAPABILITIES: Record<string, Capability> = {
  'resume-tailoring': {
    slug: 'resume-tailoring',
    eyebrow: 'Resume tailoring',
    h1: 'Tailor your resume to every job - in seconds, not hours',
    heroSub:
      'A single master resume rarely fits every role. FitWright reshapes yours to match a specific job description with AI - surfacing the right experience and keywords, scoring the fit, and showing every change so you stay in control.',
    metaTitle: 'Resume Tailoring - Tailor Your Resume to Any Job with AI',
    metaDescription:
      'Tailor your resume to any job with AI. FitWright analyzes the role, scores the fit, and reshapes your resume - every change reviewable. Free & open source.',
    socialTitle: 'Resume Tailoring with AI - FitWright',
    keywordGroup: 'tailoring',
    definitionHeading: 'What is resume tailoring?',
    definition: [
      'Resume tailoring is the practice of adapting one resume to a specific job description. Instead of sending the same generic document everywhere, you emphasize the experience most relevant to the role, mirror the wording of the required skills, and structure the resume so the fit is clear to both applicant tracking systems (ATS) and the people who read it.',
      'Done by hand, this takes real time for every application. FitWright automates the mechanical parts - analysis, keyword matching, and a first-draft rewrite - while keeping you as the editor of record. It reshapes your real experience; it does not invent new experience.',
    ],
    howToName: 'How to tailor your resume to a job',
    howToDescription:
      'Add your resume, paste the job description, tailor with AI, then review and export.',
    steps: [
      {
        icon: 'upload',
        title: 'Add your resume',
        body: 'Upload a PDF or DOCX, or build one with the guided wizard. This is your source of truth - FitWright only reshapes what is already there.',
      },
      {
        icon: 'search',
        title: 'Paste the job description',
        body: 'FitWright analyzes the posting, detects the role, and extracts the skills and keywords that matter for this specific job.',
      },
      {
        icon: 'sparkles',
        title: 'Tailor with AI',
        body: 'Using your own API key, the AI rewrites and reorders your experience to fit the role, and returns a match score with keyword, skills, and section sub-scores.',
      },
      {
        icon: 'pen',
        title: 'Review, refine & export',
        body: 'Every change is shown as a clear diff you can accept, edit, or discard. Then export a clean, ATS-friendly PDF and track the application.',
      },
    ],
    outcomesHeading: 'Relevance you can see and verify',
    outcomesSub:
      'Tailoring is only useful if it is honest and reviewable. FitWright makes both first-class.',
    outcomes: [
      {
        icon: 'gauge',
        title: 'An honest match score',
        body: 'A transparent fit score for each job, broken down into keyword, skills, and section sub-scores - not a vanity number.',
      },
      {
        icon: 'list',
        title: 'Matched vs. missing keywords',
        body: 'See exactly which skills and keywords the job wants that your resume already covers, and which are genuinely missing.',
      },
      {
        icon: 'eye',
        title: 'A reviewable diff',
        body: 'Every AI edit is explainable and reversible. You stay in control of the final wording.',
      },
      {
        icon: 'badge',
        title: 'Truthful output',
        body: 'AI reshapes your real experience for relevance. It never invents jobs, skills, or credentials you do not have.',
      },
    ],
    faqs: [
      {
        q: 'What is resume tailoring?',
        a: 'Resume tailoring is the practice of adapting a single resume to a specific job description - emphasizing the most relevant experience, matching the wording of required skills, and reordering sections so the fit is obvious to both applicant tracking systems (ATS) and human reviewers.',
      },
      {
        q: 'How is tailoring different from writing a new resume?',
        a: 'You keep one master resume. Tailoring produces a job-specific variant from it in seconds, rather than rewriting from scratch for every application. FitWright reshapes your existing content instead of starting over.',
      },
      {
        q: 'Does FitWright invent experience to match the job?',
        a: 'No. FitWright reshapes and re-emphasizes the experience you already have. It never fabricates jobs, skills, dates, or credentials. You review every change before it is applied.',
      },
      {
        q: 'Will a tailored resume pass ATS filters?',
        a: 'Tailoring improves keyword and skills alignment and keeps a clean, parseable structure, which is what ATS software screens for. FitWright also shows a match score so you can see the alignment before you apply. No tool can guarantee an interview, but relevance and structure are the levers ATS actually measures.',
      },
      {
        q: 'Do I need my own AI key to tailor resumes?',
        a: 'Yes - FitWright is bring-your-own-key. You connect the provider you prefer (OpenAI, Anthropic, Google Gemini, OpenRouter, DeepSeek, Groq, or a local model via Ollama), so you control the model, the cost, and where your data goes.',
      },
    ],
    ctaHeading: 'Ready to tailor your resume?',
    ctaSub: 'Free, open source, and private. Bring your own API key and start now.',
    primaryCtaLabel: 'Start tailoring',
  },

  'ats-resume-checker': {
    slug: 'ats-resume-checker',
    eyebrow: 'ATS resume checker',
    h1: 'Check your resume against any job - with an honest ATS score',
    heroSub:
      'Applicant tracking systems screen resumes on keywords and structure before a human reads them. FitWright scores your resume against a specific job and shows exactly what is matched and what is missing - so you fix the real gaps.',
    metaTitle: 'ATS Resume Checker - Score Your Resume Against Any Job',
    metaDescription:
      "Score your resume against any job with FitWright's ATS checker: an honest match score plus the keywords and skills you're missing. Free & open source.",
    socialTitle: 'ATS Resume Checker - FitWright',
    keywordGroup: 'atsChecker',
    definitionHeading: 'What is an ATS resume checker?',
    definition: [
      'An applicant tracking system (ATS) is the software most employers use to receive and filter job applications. It parses your resume and screens it for the keywords, skills, and structure a role requires - often before a recruiter sees it. An ATS resume checker estimates how well your resume matches a specific job so you can improve the fit before applying.',
      'FitWright checks your resume against the exact job description you paste in. It returns a transparent match score and a breakdown of matched versus missing keywords and skills - no black box, and no fake "ATS pass" guarantees.',
    ],
    howToName: 'How to check your resume against a job',
    howToDescription:
      'Add your resume, paste the job description, and review your match score and missing keywords.',
    steps: [
      {
        icon: 'upload',
        title: 'Add your resume',
        body: 'Upload a PDF or DOCX, or build one in the editor. FitWright parses it into clean, structured sections.',
      },
      {
        icon: 'search',
        title: 'Paste the job description',
        body: 'The role is analyzed to extract required skills and the keywords an ATS is likely to screen for.',
      },
      {
        icon: 'gauge',
        title: 'Get your match score',
        body: 'See an overall fit score with keyword, skills, and section sub-scores - a clear, honest read on alignment.',
      },
      {
        icon: 'list',
        title: 'Fix the real gaps',
        body: 'Review matched versus missing terms, then tailor your resume to close the genuine gaps - truthfully.',
      },
    ],
    outcomesHeading: 'A clear read on fit - before you apply',
    outcomesSub:
      'No vanity metrics and no false guarantees. Just the signals an ATS actually measures.',
    outcomes: [
      {
        icon: 'gauge',
        title: 'Transparent sub-scores',
        body: 'Keyword, skills, and section sub-scores make the overall number explainable, not a mystery.',
      },
      {
        icon: 'list',
        title: 'Matched vs. missing keywords',
        body: 'See which required terms your resume already covers and which are genuinely absent.',
      },
      {
        icon: 'file',
        title: 'Structure & parseability checks',
        body: 'Clean, ATS-friendly formatting so your content parses correctly instead of getting mangled.',
      },
      {
        icon: 'shield',
        title: 'Honest by design',
        body: 'FitWright never claims a guaranteed "ATS pass". It shows real alignment so you make informed edits.',
      },
    ],
    faqs: [
      {
        q: 'What is an ATS score?',
        a: 'An ATS score estimates how well a resume matches a specific job description, based on the keywords, skills, and structure that applicant tracking systems screen for. FitWright breaks its score into keyword, skills, and section sub-scores so the number is explainable.',
      },
      {
        q: 'Does a high score guarantee an interview?',
        a: 'No. No tool can guarantee an interview - hiring depends on many human factors. A strong match score means your resume is well-aligned and parseable for the systems that screen it, which is the part software actually measures.',
      },
      {
        q: 'How does FitWright decide what is "missing"?',
        a: 'It extracts the skills and keywords emphasized in the job description and compares them against your resume, then lists the required terms your resume does not currently cover so you can address the real gaps truthfully.',
      },
      {
        q: 'Is the ATS checker free?',
        a: 'Yes. FitWright is free and open source. You connect your own AI provider key, so you control the model and cost.',
      },
    ],
    ctaHeading: 'Check your resume against a job',
    ctaSub: 'See your match score and missing keywords in seconds. Free and open source.',
    primaryCtaLabel: 'Check my resume',
  },

  'cover-letter-generator': {
    slug: 'cover-letter-generator',
    eyebrow: 'Cover letter generator',
    h1: 'Generate a tailored cover letter grounded in your resume',
    heroSub:
      'Write a cover letter that actually fits the job - drawn from your real resume and the specific role, not a generic template. Edit it freely, then export a clean PDF.',
    metaTitle: 'Cover Letter Generator - AI Cover Letters from Your Resume',
    metaDescription:
      'Generate a tailored cover letter grounded in your resume and the job. Edit every line and export to PDF. Truthful by design. Free & open source.',
    socialTitle: 'AI Cover Letter Generator - FitWright',
    keywordGroup: 'coverLetter',
    definitionHeading: 'What is a tailored cover letter?',
    definition: [
      'A cover letter is a short, role-specific message that introduces you to an employer and explains why your experience fits the job. A tailored cover letter references the actual role and draws on your real background, rather than reusing a generic paragraph for every application.',
      'FitWright generates a first draft grounded in two things you already have: your resume and the job description. It connects your genuine experience to what the role asks for - and every word is yours to edit before you send or export it.',
    ],
    howToName: 'How to generate a cover letter',
    howToDescription:
      'Add your resume, paste the job, generate a grounded draft, then edit and export.',
    steps: [
      {
        icon: 'upload',
        title: 'Add your resume',
        body: 'Your resume is the factual basis for the letter, so the draft reflects your real experience.',
      },
      {
        icon: 'search',
        title: 'Paste the job description',
        body: 'FitWright reads the role so the letter speaks to what this specific employer is looking for.',
      },
      {
        icon: 'mail',
        title: 'Generate a grounded draft',
        body: 'Using your own API key, the AI writes a tailored first draft connecting your experience to the role.',
      },
      {
        icon: 'pen',
        title: 'Edit & export to PDF',
        body: 'Refine the tone and details, then export a clean PDF ready to attach to your application.',
      },
    ],
    outcomesHeading: 'A letter that fits - and stays yours',
    outcomesSub: 'Grounded in your real experience, fully editable, and export-ready.',
    outcomes: [
      {
        icon: 'file',
        title: 'Grounded in your resume',
        body: 'The draft is built from your actual experience and the job - not a generic, one-size-fits-all template.',
      },
      {
        icon: 'pen',
        title: 'Fully editable',
        body: 'Every line is yours to change. The AI drafts; you decide the final wording.',
      },
      {
        icon: 'badge',
        title: 'Truthful by design',
        body: 'It connects real experience to the role and never invents achievements you do not have.',
      },
      {
        icon: 'sparkles',
        title: 'Clean PDF export',
        body: 'Export a polished, consistent PDF that matches the look of your tailored resume.',
      },
    ],
    faqs: [
      {
        q: 'Does the cover letter generator make things up?',
        a: 'No. The draft is grounded in your resume and the job description. It connects your real experience to the role and does not invent achievements, employers, or credentials. You review and edit everything.',
      },
      {
        q: 'Can I edit the generated cover letter?',
        a: 'Yes - always. The AI produces a first draft; you can rewrite, trim, or restructure any part before exporting.',
      },
      {
        q: 'Can I export the cover letter to PDF?',
        a: 'Yes. Cover letters export to a clean PDF that matches the styling of your resume.',
      },
      {
        q: 'Do I need my own AI key?',
        a: 'Yes. FitWright is bring-your-own-key, so you control the AI provider, the model, and the cost - including free local models via Ollama.',
      },
    ],
    ctaHeading: 'Write your next cover letter in minutes',
    ctaSub: 'Grounded in your resume, tailored to the job, and free to edit. Open source.',
    primaryCtaLabel: 'Generate a cover letter',
  },

  'interview-preparation': {
    slug: 'interview-preparation',
    eyebrow: 'Interview preparation',
    h1: 'Prepare for interviews with questions grounded in your resume',
    heroSub:
      'Walk into interviews ready. FitWright generates likely questions, talking points, and a role-fit analysis based on your actual resume and the specific job - so you practice what genuinely matters.',
    metaTitle: 'Interview Preparation - Resume-Grounded Practice Questions',
    metaDescription:
      'Get likely interview questions, talking points, and a role-fit analysis grounded in your resume and the job. Practice what matters. Free & open source.',
    socialTitle: 'AI Interview Preparation - FitWright',
    keywordGroup: 'interviewPrep',
    definitionHeading: 'What is resume-grounded interview preparation?',
    definition: [
      'Interview preparation is the work you do before an interview to anticipate questions and rehearse strong, specific answers. Generic question lists only go so far - the most useful practice is tied to your actual background and the specific role you are interviewing for.',
      'FitWright generates likely questions, talking points, and a role-fit analysis from your resume and the job description. Because it is grounded in your real experience, you rehearse answers that are genuinely yours to give.',
    ],
    howToName: 'How to prepare for an interview',
    howToDescription:
      'Add your resume, paste the job, and generate resume-grounded questions and talking points.',
    steps: [
      {
        icon: 'upload',
        title: 'Add your resume',
        body: 'Your experience is the basis for the questions, so practice stays relevant to your background.',
      },
      {
        icon: 'search',
        title: 'Paste the job description',
        body: 'The role is analyzed so preparation targets what this specific interview is likely to cover.',
      },
      {
        icon: 'message',
        title: 'Generate questions & talking points',
        body: 'Using your own API key, FitWright produces likely questions, suggested talking points, and a role-fit analysis.',
      },
      {
        icon: 'brain',
        title: 'Rehearse with focus',
        body: 'Practice the answers that matter most, using your real accomplishments as evidence.',
      },
    ],
    outcomesHeading: 'Practice what actually matters',
    outcomesSub: 'Preparation tied to your resume and the role - not a generic checklist.',
    outcomes: [
      {
        icon: 'message',
        title: 'Likely questions',
        body: 'Questions an interviewer is likely to ask for this role, drawn from the job and your background.',
      },
      {
        icon: 'list',
        title: 'Talking points',
        body: 'Suggested angles and evidence from your resume to structure strong, specific answers.',
      },
      {
        icon: 'target',
        title: 'Role-fit analysis',
        body: 'A clear view of where your experience aligns with the role and where to prepare more.',
      },
      {
        icon: 'badge',
        title: 'Grounded in your resume',
        body: 'Prep is based on your genuine experience, so the answers you rehearse are truly yours.',
      },
    ],
    faqs: [
      {
        q: 'How does FitWright generate interview questions?',
        a: 'It analyzes your resume and the job description together, then produces likely questions, talking points, and a role-fit analysis grounded in your real experience and the specific role.',
      },
      {
        q: 'Are the questions specific to my resume and the job?',
        a: 'Yes. Preparation is generated from both your resume and the pasted job description, so it targets what this particular interview is likely to cover rather than generic trivia.',
      },
      {
        q: 'Does it write my answers for me?',
        a: 'It suggests talking points and angles based on your experience. The answers are yours to shape - the goal is focused, honest practice, not a script of invented achievements.',
      },
      {
        q: 'Is interview preparation free?',
        a: 'Yes. FitWright is free and open source, and uses your own AI provider key, so you control the model and cost.',
      },
    ],
    ctaHeading: 'Walk into your next interview prepared',
    ctaSub: 'Resume-grounded questions and talking points, generated on demand. Open source.',
    primaryCtaLabel: 'Prepare for an interview',
  },
};

/** All capability slugs, in navigational order (tailoring first). */
export const CAPABILITY_SLUGS = [
  'resume-tailoring',
  'ats-resume-checker',
  'cover-letter-generator',
  'interview-preparation',
] as const;

/** Short labels for nav/cross-links. */
export const CAPABILITY_NAV: ReadonlyArray<{ slug: string; label: string }> = [
  { slug: 'resume-tailoring', label: 'Resume tailoring' },
  { slug: 'ats-resume-checker', label: 'ATS resume checker' },
  { slug: 'cover-letter-generator', label: 'Cover letter generator' },
  { slug: 'interview-preparation', label: 'Interview preparation' },
];
