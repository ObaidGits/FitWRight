import type { Metadata } from 'next';
import { JsonLd } from '@/lib/seo/json-ld';
import { buildMetadata } from '@/lib/seo/metadata';
import { KEYWORDS } from '@/lib/seo/page-keywords';
import { breadcrumbSchema } from '@/lib/seo/structured-data';

export const metadata: Metadata = buildMetadata({
  title: 'Privacy Policy',
  description:
    'How FitWright collects, uses, stores, and protects your personal data, your rights under India\u2019s Digital Personal Data Protection Act, 2023, and how to reach us for privacy requests.',
  path: '/privacy',
  keywords: KEYWORDS.privacy,
});

const EFFECTIVE_DATE = '16 July 2026';

type Section = {
  heading: string;
  paragraphs?: string[];
  list?: string[];
};

const SECTIONS: Section[] = [
  {
    heading: '1. Introduction',
    paragraphs: [
      'This Privacy Policy explains how personal data is collected, used, stored, shared, and protected when you use the FitWright application hosted at fitwright.tech (the "Service"). It is written to be consistent with the Digital Personal Data Protection Act, 2023 (the "DPDP Act"), the Information Technology Act, 2000, and the rules made under them, as applicable in India.',
      'By creating an account or using the Service, you acknowledge that you have read and understood this Policy. If you do not agree with it, please do not use the Service.',
    ],
  },
  {
    heading: '2. Who we are',
    paragraphs: [
      'The hosted Service at fitwright.tech is operated by Obaidullah Zeeshan, an individual developer ("we", "us", "our"), who acts as the Data Fiduciary for personal data processed through this hosted instance.',
      'FitWright is free and open-source software released under the Apache License 2.0. The source code is publicly available on GitHub. Anyone may self-host their own instance. If you use a self-hosted copy operated by someone else, that operator \u2014 not us \u2014 is the Data Fiduciary responsible for your data, and this Policy may not apply to them.',
    ],
  },
  {
    heading: '3. Data we collect',
    paragraphs: ['We collect only the data needed to provide the Service:'],
    list: [
      'Account data: your name, email address, and a securely hashed password. If you sign in with Google, we receive your basic profile (name, email, and profile picture) from Google.',
      'Profile and resume content: the information you add or upload \u2014 resumes, job descriptions, work experience, education, skills, projects, cover letters, interview-prep notes, and application-tracker entries.',
      'Optional profile photo: if you upload one, it is stored with our image provider.',
      'Configuration data: settings you choose, including any AI provider API key you add (stored encrypted).',
      'Technical and security data: session and CSRF cookies, a one-way hash of your IP address, timestamps, and audit/security logs used to keep your account safe and the Service reliable.',
    ],
  },
  {
    heading: '4. How and why we use your data',
    paragraphs: [
      'We process your personal data for the following purposes, based on the consent you provide when you use the Service and on our legitimate need to operate it:',
    ],
    list: [
      'To create and secure your account and authenticate you.',
      'To store, tailor, and export your resumes, cover letters, and related documents.',
      'To generate AI-assisted content when you explicitly request it.',
      'To send essential service emails (email verification, password reset, and security notices).',
      'To prevent abuse, enforce rate limits, and maintain the security and integrity of the Service.',
    ],
  },
  {
    heading: '5. AI processing',
    paragraphs: [
      'When you start a generation (for example, tailoring a resume or drafting a cover letter), the relevant content you submit is sent to a third-party Large Language Model (LLM) provider configured for the Service, solely to produce your requested output. This happens only when you initiate the action.',
      'We do not use your content to train any AI models. The third-party provider processes your content under its own terms and privacy policy. AI output can be inaccurate; you are responsible for reviewing it before use.',
    ],
  },
  {
    heading: '6. Service providers we rely on',
    paragraphs: [
      'We use a small number of reputable processors to run the Service. They process data only on our instructions and only to provide their function:',
    ],
    list: [
      'Cloud hosting for the application.',
      'A managed PostgreSQL database provider that stores your account, resume, and profile data.',
      'A media/CDN provider that stores uploaded images (such as your profile photo).',
      'An email delivery (SMTP) provider for transactional emails.',
      'Google, if you choose Google Sign-In.',
      'The AI/LLM provider described in Section 5.',
    ],
  },
  {
    heading: '7. Cross-border data transfer',
    paragraphs: [
      'Some of the providers above operate servers outside India. As a result, your personal data may be stored or processed in other countries. Where this occurs, we rely on providers that offer recognised safeguards, and we transfer data only as permitted under applicable Indian law.',
    ],
  },
  {
    heading: '8. Cookies',
    paragraphs: [
      'We use only essential cookies required for the Service to function \u2014 primarily a secure session cookie and a CSRF-protection cookie. We do not use third-party advertising or cross-site tracking cookies. Because these cookies are strictly necessary, the Service will not work correctly if they are blocked.',
    ],
  },
  {
    heading: '9. Data retention',
    paragraphs: [
      'We retain your personal data for as long as your account is active or as needed to provide the Service. When you delete a resume or other content, the associated records are removed. When you delete your account, your personal data is deleted, except where we are required to retain limited information to comply with a legal obligation or to resolve disputes. Backups and logs are retained only for a limited period and then purged.',
    ],
  },
  {
    heading: '10. How we protect your data',
    paragraphs: [
      'We apply reasonable security practices appropriate to the nature of the data, including: encryption in transit (HTTPS/TLS); passwords stored using the Argon2id hashing algorithm (never in plain text); AI provider API keys encrypted at rest and never returned to your browser; user-scoped access controls so you can only access your own data; and audit logging of sensitive actions.',
      'No method of transmission or storage is completely secure. While we work to protect your data, we cannot guarantee absolute security.',
    ],
  },
  {
    heading: '11. Your rights',
    paragraphs: ['Subject to the DPDP Act and applicable law, you have the right to:'],
    list: [
      'Access the personal data we hold about you.',
      'Correct or update inaccurate or incomplete data.',
      'Erase your data by deleting content or your account.',
      'Withdraw consent at any time (this may limit or end your ability to use the Service).',
      'Nominate another individual to exercise your rights in the event of your death or incapacity.',
      'Raise a grievance with us and, if unsatisfied, escalate to the Data Protection Board of India.',
    ],
  },
  {
    heading: '12. Children',
    paragraphs: [
      'The Service is intended for users who are 18 years of age or older and is not directed at children. We do not knowingly collect the personal data of children. If we learn that we have collected such data without appropriate consent, we will delete it.',
    ],
  },
  {
    heading: '13. Grievance redressal and contact',
    paragraphs: [
      'If you have any questions, requests, or complaints about your personal data or this Policy, you can contact us through the Contact page on this website. We will acknowledge and address grievances within the timelines required under applicable Indian law.',
      'For self-hosted instances operated by others, please contact the operator of that instance.',
    ],
  },
  {
    heading: '14. Changes to this Policy',
    paragraphs: [
      'We may update this Policy from time to time to reflect changes in the Service or the law. When we do, we will revise the "Effective date" above. Significant changes will be communicated through the Service where reasonably possible. Your continued use after an update constitutes acceptance of the revised Policy.',
    ],
  },
];

export default function PrivacyPage() {
  return (
    <article className="mx-auto w-full max-w-3xl px-4 py-16 md:px-8">
      <JsonLd
        data={breadcrumbSchema([
          { name: 'Home', path: '/' },
          { name: 'Privacy Policy', path: '/privacy' },
        ])}
      />
      <h1 className="text-3xl font-semibold">Privacy Policy</h1>
      <p className="mt-2 text-sm text-[var(--muted-foreground)]">
        Effective date: {EFFECTIVE_DATE}
      </p>

      <div className="mt-8 space-y-8 text-[var(--foreground)]">
        {SECTIONS.map((section) => (
          <section key={section.heading} className="space-y-3">
            <h2 className="text-lg font-semibold">{section.heading}</h2>
            {section.paragraphs?.map((p, i) => (
              <p key={i} className="text-[var(--muted-foreground)]">
                {p}
              </p>
            ))}
            {section.list ? (
              <ul className="list-disc space-y-1 pl-6 text-[var(--muted-foreground)]">
                {section.list.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            ) : null}
          </section>
        ))}
      </div>
    </article>
  );
}
