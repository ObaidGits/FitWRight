/**
 * Reusable schema.org (JSON-LD) generators.
 *
 * Every generator returns a plain, serializable object designed to be rendered
 * through the `<JsonLd>` server component. Relationships use stable `@id`
 * anchors (Organization <-> WebSite <-> SoftwareApplication <-> Person) so search
 * engines and AI retrieval systems can resolve the entity graph unambiguously.
 *
 * Content is truthful to the product: FitWright is free, open source, and
 * bring-your-own-key, so `Offer` price is 0 and no fabricated ratings are used.
 */
import {
  SITE_URL,
  SITE_NAME,
  SITE_DESCRIPTION,
  AUTHOR,
  GITHUB_REPO,
  SOCIAL_LINKS,
  absoluteUrl,
} from './config';

/** Stable entity anchors within the site's linked-data graph. */
export const ORG_ID = `${SITE_URL}/#organization`;
export const SITE_ID = `${SITE_URL}/#website`;
export const APP_ID = `${SITE_URL}/#software`;
export const PERSON_ID = `${AUTHOR.url}#person`;

type JsonLdObject = Record<string, unknown>;

/** The developer/founder as a schema.org Person (EEAT author identity). */
export function personSchema(): JsonLdObject {
  return {
    '@context': 'https://schema.org',
    '@type': 'Person',
    '@id': PERSON_ID,
    name: AUTHOR.name,
    jobTitle: AUTHOR.jobTitle,
    url: AUTHOR.url,
    sameAs: [AUTHOR.linkedin, AUTHOR.github],
  };
}

/** Organization behind the product - founder-linked for credibility. */
export function organizationSchema(): JsonLdObject {
  return {
    '@context': 'https://schema.org',
    '@type': 'Organization',
    '@id': ORG_ID,
    name: SITE_NAME,
    url: `${SITE_URL}/`,
    logo: absoluteUrl('/logo.svg'),
    description: SITE_DESCRIPTION,
    founder: { '@id': PERSON_ID },
    sameAs: [...SOCIAL_LINKS, GITHUB_REPO],
  };
}

/** The website entity. */
export function websiteSchema(): JsonLdObject {
  return {
    '@context': 'https://schema.org',
    '@type': 'WebSite',
    '@id': SITE_ID,
    name: SITE_NAME,
    url: `${SITE_URL}/`,
    description: SITE_DESCRIPTION,
    publisher: { '@id': ORG_ID },
    inLanguage: 'en-US',
  };
}

/**
 * The product as a SoftwareApplication. `offers` at price 0 truthfully reflects
 * that FitWright is free and open source (bring-your-own-key).
 */
export function softwareApplicationSchema(): JsonLdObject {
  return {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    '@id': APP_ID,
    name: SITE_NAME,
    url: `${SITE_URL}/`,
    applicationCategory: 'BusinessApplication',
    applicationSubCategory: 'Resume Builder',
    operatingSystem: 'Web, Windows, macOS, Linux',
    description: SITE_DESCRIPTION,
    softwareVersion: '1.2',
    license: 'https://www.apache.org/licenses/LICENSE-2.0',
    isAccessibleForFree: true,
    author: { '@id': PERSON_ID },
    publisher: { '@id': ORG_ID },
    offers: {
      '@type': 'Offer',
      price: '0',
      priceCurrency: 'USD',
    },
    featureList: [
      'AI resume tailoring',
      'ATS match scoring',
      'Cover letter generation',
      'Interview preparation',
      'Job description analysis',
      'Application tracking',
      'PDF export',
      'Bring your own API key',
    ],
  };
}

/** FAQPage from a list of question/answer pairs (mirrors on-page FAQ UI). */
export function faqPageSchema(items: ReadonlyArray<{ q: string; a: string }>): JsonLdObject {
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: items.map(({ q, a }) => ({
      '@type': 'Question',
      name: q,
      acceptedAnswer: { '@type': 'Answer', text: a },
    })),
  };
}

/**
 * HowTo from ordered steps. Steps MUST mirror on-page content (a structured-data
 * requirement). Primarily benefits AI/LLM comprehension and non-Google engines.
 */
export function howToSchema(input: {
  name: string;
  description: string;
  steps: ReadonlyArray<{ name: string; text: string }>;
}): JsonLdObject {
  return {
    '@context': 'https://schema.org',
    '@type': 'HowTo',
    name: input.name,
    description: input.description,
    step: input.steps.map((s, i) => ({
      '@type': 'HowToStep',
      position: i + 1,
      name: s.name,
      text: s.text,
    })),
  };
}

/** BreadcrumbList from ordered `{ name, path }` crumbs. */
export function breadcrumbSchema(
  crumbs: ReadonlyArray<{ name: string; path: string }>
): JsonLdObject {
  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: crumbs.map((c, i) => ({
      '@type': 'ListItem',
      position: i + 1,
      name: c.name,
      item: absoluteUrl(c.path),
    })),
  };
}

/** ContactPage with the developer as the reachable entity. */
export function contactPageSchema(path = '/contact'): JsonLdObject {
  return {
    '@context': 'https://schema.org',
    '@type': 'ContactPage',
    name: `Contact ${SITE_NAME}`,
    url: absoluteUrl(path),
    description:
      'Reach out about AI engineering, full-stack development, collaboration, or FitWright.',
    isPartOf: { '@id': SITE_ID },
    mainEntity: { '@id': PERSON_ID },
  };
}

/**
 * The open-source project as SoftwareSourceCode - a strong, truthful EEAT +
 * knowledge-graph signal (real public repository, Apache-2.0 licensed).
 */
export function softwareSourceCodeSchema(): JsonLdObject {
  return {
    '@context': 'https://schema.org',
    '@type': 'SoftwareSourceCode',
    name: `${SITE_NAME} - source code`,
    codeRepository: GITHUB_REPO,
    url: GITHUB_REPO,
    programmingLanguage: ['TypeScript', 'Python'],
    license: 'https://www.apache.org/licenses/LICENSE-2.0',
    author: { '@id': PERSON_ID },
    about: { '@id': APP_ID },
  };
}

/** Generic WebPage - reusable for future static content pages. */
export function webPageSchema(input: {
  name: string;
  path: string;
  description?: string;
}): JsonLdObject {
  return {
    '@context': 'https://schema.org',
    '@type': 'WebPage',
    name: input.name,
    url: absoluteUrl(input.path),
    description: input.description,
    isPartOf: { '@id': SITE_ID },
    inLanguage: 'en-US',
  };
}

/** Generic CollectionPage - reusable for portfolios, template galleries, etc. */
export function collectionPageSchema(input: {
  name: string;
  path: string;
  about?: unknown;
}): JsonLdObject {
  return {
    '@context': 'https://schema.org',
    '@type': 'CollectionPage',
    name: input.name,
    url: absoluteUrl(input.path),
    isPartOf: { '@id': SITE_ID },
    ...(input.about ? { about: input.about } : {}),
  };
}

/** ProfilePage exposing the developer Person (used on /connect). */
export function profilePageSchema(path = '/connect'): JsonLdObject {
  return {
    '@context': 'https://schema.org',
    '@type': 'ProfilePage',
    name: `Connect with the developer - ${SITE_NAME}`,
    url: absoluteUrl(path),
    isPartOf: { '@id': SITE_ID },
    mainEntity: personSchema(),
  };
}
