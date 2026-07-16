import type { Metadata } from 'next';
import { JsonLd } from '@/lib/seo/json-ld';
import { buildMetadata } from '@/lib/seo/metadata';
import { KEYWORDS } from '@/lib/seo/page-keywords';
import { breadcrumbSchema } from '@/lib/seo/structured-data';

export const metadata: Metadata = buildMetadata({
  title: 'Terms of Use',
  description:
    'The terms governing your use of FitWright: an open-source (Apache 2.0) resume tool provided as-is, your responsibilities, acceptable use under Indian law, and limitations of liability.',
  path: '/terms',
  keywords: KEYWORDS.terms,
});

const EFFECTIVE_DATE = '16 July 2026';

type Section = {
  heading: string;
  paragraphs?: string[];
  list?: string[];
};

const SECTIONS: Section[] = [
  {
    heading: '1. Acceptance of these Terms',
    paragraphs: [
      'These Terms of Use ("Terms") govern your access to and use of the FitWright application hosted at fitwright.tech (the "Service"). By creating an account or using the Service, you agree to be bound by these Terms. If you do not agree, do not use the Service.',
      'These Terms are an electronic record under the Information Technology Act, 2000, and do not require any physical or digital signature.',
    ],
  },
  {
    heading: '2. About FitWright and open-source licensing',
    paragraphs: [
      'FitWright is free and open-source software released under the Apache License 2.0. The software itself is provided under that license, and your rights to the source code are governed by it.',
      'These Terms govern only the hosted Service at fitwright.tech operated by Obaidullah Zeeshan. You are free to inspect, modify, and self-host the software under the Apache License 2.0. If you self-host, you are solely responsible for your instance, its data, and its compliance with applicable law.',
    ],
  },
  {
    heading: '3. Eligibility',
    paragraphs: [
      'You must be at least 18 years old and legally capable of entering into a binding contract to use the Service. By using the Service, you represent that you meet these requirements.',
    ],
  },
  {
    heading: '4. Your account',
    paragraphs: [
      'You are responsible for maintaining the confidentiality of your login credentials and for all activity under your account. Provide accurate information when registering, and notify us promptly of any unauthorised use. We may suspend or terminate accounts that violate these Terms.',
    ],
  },
  {
    heading: '5. Acceptable use',
    paragraphs: ['You agree not to use the Service to:'],
    list: [
      'Upload, generate, or share content that is unlawful, defamatory, obscene, fraudulent, infringing, or that violates any law in force in India.',
      'Impersonate any person or misrepresent your identity, qualifications, or experience in a way that is unlawful.',
      'Infringe the intellectual property or privacy rights of others.',
      'Attempt to gain unauthorised access to the Service, other accounts, or its infrastructure, or disrupt its operation.',
      'Introduce malware, or use automated means to abuse, overload, or scrape the Service.',
      'Use the Service in violation of the Information Technology Act, 2000 and the rules made under it, including the Intermediary Guidelines.',
    ],
  },
  {
    heading: '6. Your content',
    paragraphs: [
      'You retain ownership of the content you upload or create (such as resumes and job descriptions). You grant us a limited licence to store and process that content solely to provide the Service to you.',
      'You are responsible for ensuring that your content is accurate and truthful and that you have the right to use it. FitWright is designed to tailor your genuine experience, not to fabricate it; you must not use it to create false or misleading claims.',
    ],
  },
  {
    heading: '7. AI-generated content',
    paragraphs: [
      'The Service uses third-party AI models to assist you. AI output may be inaccurate, incomplete, or unsuitable, and is provided only as a draft. You are responsible for reviewing, editing, and verifying any AI-assisted content before you rely on or submit it. We do not guarantee any particular outcome, including interviews or employment.',
    ],
  },
  {
    heading: '8. Third-party services',
    paragraphs: [
      'The Service depends on third-party providers (for hosting, database, storage, email, sign-in, and AI). Your use of those features may be subject to the respective third parties\u2019 terms. We are not responsible for third-party services that are outside our control.',
    ],
  },
  {
    heading: '9. Intellectual property',
    paragraphs: [
      'The FitWright name, branding, and the compilation of the Service are protected by applicable laws. The underlying software is licensed under the Apache License 2.0. Nothing in these Terms transfers ownership of our marks to you.',
    ],
  },
  {
    heading: '10. Disclaimer of warranties',
    paragraphs: [
      'The Service is provided on an "as is" and "as available" basis, without warranties of any kind, whether express or implied, including fitness for a particular purpose, accuracy, or uninterrupted availability, to the maximum extent permitted by law.',
    ],
  },
  {
    heading: '11. Limitation of liability',
    paragraphs: [
      'To the maximum extent permitted by law, we shall not be liable for any indirect, incidental, special, or consequential damages, or for loss of data, opportunities, or profits, arising out of your use of or inability to use the Service. Because the Service is provided free of charge, our total aggregate liability, if any, is limited to the extent permitted by law.',
    ],
  },
  {
    heading: '12. Indemnity',
    paragraphs: [
      'You agree to indemnify and hold us harmless from any claims, damages, or expenses arising from your content, your use of the Service, or your breach of these Terms or of any applicable law.',
    ],
  },
  {
    heading: '13. Termination',
    paragraphs: [
      'You may stop using the Service and delete your account at any time. We may suspend or terminate your access if you violate these Terms or applicable law, or if required to protect the Service or other users. Provisions that by their nature should survive termination will survive.',
    ],
  },
  {
    heading: '14. Governing law and jurisdiction',
    paragraphs: [
      'These Terms are governed by and construed in accordance with the laws of India. Subject to the grievance process below, the courts of competent jurisdiction in India shall have exclusive jurisdiction over any disputes arising out of or relating to these Terms or the Service.',
    ],
  },
  {
    heading: '15. Grievance redressal',
    paragraphs: [
      'In accordance with the Information Technology Act, 2000 and the rules made under it, any grievance regarding content or your use of the Service may be raised through the Contact page on this website. We will acknowledge your grievance and endeavour to resolve it within the timelines prescribed under applicable law.',
    ],
  },
  {
    heading: '16. Changes to these Terms',
    paragraphs: [
      'We may revise these Terms from time to time. When we do, we will update the "Effective date" above and, where reasonably possible, notify you through the Service. Your continued use after changes take effect constitutes acceptance of the revised Terms.',
    ],
  },
];

export default function TermsPage() {
  return (
    <article className="mx-auto w-full max-w-3xl px-4 py-16 md:px-8">
      <JsonLd
        data={breadcrumbSchema([
          { name: 'Home', path: '/' },
          { name: 'Terms of Use', path: '/terms' },
        ])}
      />
      <h1 className="text-3xl font-semibold">Terms of Use</h1>
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
