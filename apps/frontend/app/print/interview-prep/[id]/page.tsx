/**
 * Interview Prep Print Page
 *
 * Renders saved interview preparation (role fit, likely questions, project
 * follow-ups, skill gaps, talking points) for PDF generation via headless
 * Chromium. Mirrors the cover-letter print page's data-fetch pattern.
 */

import { API_BASE } from '@/lib/api/client';

const PAGE_DIMENSIONS = {
  A4: { width: 210, height: 297 },
  LETTER: { width: 215.9, height: 279.4 },
} as const;

type PageSize = 'A4' | 'LETTER';

type PageProps = {
  params: Promise<{ id: string }>;
  searchParams?: Promise<{
    pageSize?: string;
    lang?: string;
    print_token?: string;
  }>;
};

interface InterviewPrepQuestion {
  question: string;
  focus_area?: string | null;
  suggested_answer_points: string[];
}

interface InterviewPrepSkillGap {
  skill: string;
  why_it_matters: string;
  preparation_suggestion: string;
}

interface InterviewPrepData {
  role_fit_analysis: string[];
  resume_questions: InterviewPrepQuestion[];
  project_follow_ups: InterviewPrepQuestion[];
  skill_gaps: InterviewPrepSkillGap[];
  talking_points: string[];
}

interface PersonalInfo {
  name?: string;
  title?: string;
}

interface InterviewPrepPageData {
  prep: InterviewPrepData;
  personalInfo: PersonalInfo;
}

async function fetchInterviewPrepData(
  resumeId: string,
  printToken?: string
): Promise<InterviewPrepPageData> {
  let url: string;
  const init: RequestInit = { cache: 'no-store' };
  if (printToken) {
    url = `${API_BASE}/resumes/print-data?resume_id=${encodeURIComponent(resumeId)}&token=${encodeURIComponent(printToken)}`;
  } else {
    url = `${API_BASE}/resumes?resume_id=${encodeURIComponent(resumeId)}`;
    const { headers } = await import('next/headers');
    const cookie = (await headers()).get('cookie');
    if (cookie) {
      init.headers = { cookie };
    }
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    throw new Error(`Failed to load resume (status ${res.status}).`);
  }
  const payload = (await res.json()) as {
    data: {
      interview_prep?: InterviewPrepData | null;
      processed_resume?: { personalInfo?: PersonalInfo };
    };
  };

  return {
    prep: payload.data.interview_prep ?? {
      role_fit_analysis: [],
      resume_questions: [],
      project_follow_ups: [],
      skill_gaps: [],
      talking_points: [],
    },
    personalInfo: payload.data.processed_resume?.personalInfo || {},
  };
}

function parsePageSize(value: string | undefined): PageSize {
  if (value === 'A4' || value === 'LETTER') {
    return value;
  }
  return 'A4';
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: '6mm', pageBreakInside: 'avoid' }}>
      <h2
        style={{
          fontSize: '12pt',
          fontWeight: 'bold',
          margin: '0 0 3mm 0',
          textTransform: 'uppercase',
          letterSpacing: '0.03em',
          borderBottom: '1px solid #000',
          paddingBottom: '1.5mm',
        }}
      >
        {title}
      </h2>
      {children}
    </section>
  );
}

function BulletList({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <ul style={{ margin: 0, paddingLeft: '5mm' }}>
      {items.map((item, i) => (
        <li key={i} style={{ fontSize: '10.5pt', lineHeight: 1.5, marginBottom: '1.5mm' }}>
          {item}
        </li>
      ))}
    </ul>
  );
}

function QuestionBlock({ item }: { item: InterviewPrepQuestion }) {
  return (
    <div style={{ marginBottom: '4mm', pageBreakInside: 'avoid' }}>
      <p style={{ fontSize: '11pt', fontWeight: 'bold', margin: '0 0 1mm 0' }}>{item.question}</p>
      {item.focus_area && (
        <p
          style={{
            fontSize: '9pt',
            color: '#555',
            margin: '0 0 1.5mm 0',
            textTransform: 'uppercase',
            letterSpacing: '0.02em',
          }}
        >
          {item.focus_area}
        </p>
      )}
      <BulletList items={item.suggested_answer_points} />
    </div>
  );
}

export default async function PrintInterviewPrepPage({ params, searchParams }: PageProps) {
  const resolvedParams = await params;
  const resolvedSearchParams = searchParams ? await searchParams : undefined;

  const pageSize = parsePageSize(resolvedSearchParams?.pageSize);
  const pageDims = PAGE_DIMENSIONS[pageSize];

  const { prep, personalInfo } = await fetchInterviewPrepData(
    resolvedParams.id,
    resolvedSearchParams?.print_token
  );

  const margins = { top: 20, right: 20, bottom: 20, left: 20 };

  return (
    <div
      className="interview-prep-print bg-white"
      style={{
        width: `${pageDims.width}mm`,
        minHeight: `${pageDims.height}mm`,
        padding: `${margins.top}mm ${margins.right}mm ${margins.bottom}mm ${margins.left}mm`,
        boxSizing: 'border-box',
        fontFamily: 'Georgia, serif',
        color: '#000000',
      }}
    >
      <header
        style={{
          marginBottom: '6mm',
          paddingBottom: '3mm',
          borderBottom: '2px solid #000',
        }}
      >
        <h1 style={{ fontSize: '16pt', fontWeight: 'bold', margin: 0 }}>Interview Preparation</h1>
        {(personalInfo.name || personalInfo.title) && (
          <p style={{ fontSize: '10pt', color: '#666', margin: '1.5mm 0 0 0' }}>
            {[personalInfo.name, personalInfo.title].filter(Boolean).join(' \u00b7 ')}
          </p>
        )}
      </header>

      {prep.role_fit_analysis.length > 0 && (
        <Section title="Role fit">
          <BulletList items={prep.role_fit_analysis} />
        </Section>
      )}

      {prep.resume_questions.length > 0 && (
        <Section title="Likely questions">
          {prep.resume_questions.map((q, i) => (
            <QuestionBlock key={i} item={q} />
          ))}
        </Section>
      )}

      {prep.project_follow_ups.length > 0 && (
        <Section title="Project follow-ups">
          {prep.project_follow_ups.map((q, i) => (
            <QuestionBlock key={i} item={q} />
          ))}
        </Section>
      )}

      {prep.skill_gaps.length > 0 && (
        <Section title="Skill gaps">
          {prep.skill_gaps.map((g, i) => (
            <div key={i} style={{ marginBottom: '3.5mm', pageBreakInside: 'avoid' }}>
              <p style={{ fontSize: '11pt', fontWeight: 'bold', margin: '0 0 1mm 0' }}>{g.skill}</p>
              <p style={{ fontSize: '10.5pt', margin: '0 0 1mm 0', lineHeight: 1.5 }}>
                <strong>Why it matters: </strong>
                {g.why_it_matters}
              </p>
              <p style={{ fontSize: '10.5pt', margin: 0, lineHeight: 1.5 }}>
                <strong>How to prepare: </strong>
                {g.preparation_suggestion}
              </p>
            </div>
          ))}
        </Section>
      )}

      {prep.talking_points.length > 0 && (
        <Section title="Talking points">
          <BulletList items={prep.talking_points} />
        </Section>
      )}

      {prep.role_fit_analysis.length === 0 &&
        prep.resume_questions.length === 0 &&
        prep.project_follow_ups.length === 0 &&
        prep.skill_gaps.length === 0 &&
        prep.talking_points.length === 0 && (
          <p style={{ fontSize: '11pt', color: '#999' }}>No interview preparation available.</p>
        )}
    </div>
  );
}
