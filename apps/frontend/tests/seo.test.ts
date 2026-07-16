import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import * as React from 'react';

import {
  SITE_URL,
  SITE_NAME,
  absoluteUrl,
  OG_IMAGE,
  TWITTER_IMAGE,
  VERIFICATION,
  GITHUB_REPO,
  AUTHOR,
} from '@/lib/seo/config';
import { buildMetadata, NOINDEX } from '@/lib/seo/metadata';
import { KEYWORDS } from '@/lib/seo/page-keywords';
import { JsonLd } from '@/lib/seo/json-ld';
import {
  ORG_ID,
  SITE_ID,
  APP_ID,
  PERSON_ID,
  organizationSchema,
  websiteSchema,
  softwareApplicationSchema,
  softwareSourceCodeSchema,
  personSchema,
  faqPageSchema,
  howToSchema,
  breadcrumbSchema,
  contactPageSchema,
  profilePageSchema,
  collectionPageSchema,
  webPageSchema,
} from '@/lib/seo/structured-data';
import robots from '@/app/robots';
import sitemap from '@/app/sitemap';
import manifest from '@/app/manifest';
import {
  CAPABILITIES,
  CAPABILITY_SLUGS,
  CAPABILITY_NAV,
  capabilityPath,
} from '@/components/marketing/capabilities-data';

/**
 * SEO regression suite. Locks in the production-grade SEO contract so future
 * refactors can't silently break canonical URLs, structured-data relationships,
 * robots/sitemap coverage, or introduce keyword cannibalization.
 */

describe('config', () => {
  it('SITE_URL has no trailing slash', () => {
    expect(SITE_URL.endsWith('/')).toBe(false);
  });

  it('absoluteUrl joins paths onto the canonical origin', () => {
    expect(absoluteUrl('/')).toBe(`${SITE_URL}/`);
    expect(absoluteUrl('/contact')).toBe(`${SITE_URL}/contact`);
    // Tolerates a missing leading slash.
    expect(absoluteUrl('contact')).toBe(`${SITE_URL}/contact`);
  });

  it('social image descriptors point at the metadata routes', () => {
    expect(OG_IMAGE.url).toBe('/opengraph-image');
    expect(OG_IMAGE.width).toBe(1200);
    expect(OG_IMAGE.height).toBe(630);
    expect(TWITTER_IMAGE).toBe('/twitter-image');
  });

  it('ships the verified Google production token without bogus optional tokens', () => {
    expect(VERIFICATION.google).toMatch(/\S+/);
    expect(VERIFICATION.bing).toBeUndefined();
    expect(VERIFICATION.yandex).toBeUndefined();
  });
});

describe('buildMetadata', () => {
  const md = buildMetadata({
    title: 'Contact',
    description: 'Reach out.',
    path: '/contact',
    keywords: ['a', 'b'],
  });

  it('sets a relative canonical (resolved against metadataBase)', () => {
    expect(md.alternates?.canonical).toBe('/contact');
  });

  it('mirrors title/description into OpenGraph + Twitter', () => {
    expect(md.openGraph?.title).toBe(`Contact · ${SITE_NAME}`);
    expect(md.twitter?.title).toBe(`Contact · ${SITE_NAME}`);
    expect(md.openGraph?.description).toBe('Reach out.');
  });

  it('always attaches the branded social images', () => {
    expect(md.openGraph?.images).toEqual([OG_IMAGE]);
    expect(md.twitter?.images).toEqual([TWITTER_IMAGE]);
  });

  it('is indexable by default and noindex only when requested', () => {
    expect(md.robots).toBeUndefined();
    const priv = buildMetadata({ title: 'x', path: '/x', noindex: true });
    expect(priv.robots).toBe(NOINDEX);
  });

  it('NOINDEX blocks indexing for private surfaces', () => {
    expect(NOINDEX).toMatchObject({ index: false, follow: false });
  });

  it('strips a trailing slash from the canonical path', () => {
    const m = buildMetadata({ title: 'x', path: '/foo/' });
    expect(m.alternates?.canonical).toBe('/foo');
  });
});

describe('structured data — entity graph', () => {
  it('Organization links the founder Person and lists sameAs', () => {
    const org = organizationSchema();
    expect(org['@type']).toBe('Organization');
    expect(org['@id']).toBe(ORG_ID);
    expect(org.founder).toEqual({ '@id': PERSON_ID });
    expect(org.sameAs as string[]).toContain(GITHUB_REPO);
  });

  it('WebSite is published by the Organization', () => {
    const site = websiteSchema();
    expect(site['@type']).toBe('WebSite');
    expect(site['@id']).toBe(SITE_ID);
    expect(site.publisher).toEqual({ '@id': ORG_ID });
  });

  it('SoftwareApplication is truthfully free (Offer price 0, accessible)', () => {
    const app = softwareApplicationSchema();
    expect(app['@type']).toBe('SoftwareApplication');
    expect(app['@id']).toBe(APP_ID);
    expect(app.isAccessibleForFree).toBe(true);
    expect(app.offers).toMatchObject({ '@type': 'Offer', price: '0', priceCurrency: 'USD' });
    expect(String(app.license)).toContain('apache.org');
  });

  it('SoftwareSourceCode points at the real repository', () => {
    const src = softwareSourceCodeSchema();
    expect(src['@type']).toBe('SoftwareSourceCode');
    expect(src.codeRepository).toBe(GITHUB_REPO);
    expect(src.author).toEqual({ '@id': PERSON_ID });
  });

  it('Person exposes the founder identity + verified profiles', () => {
    const p = personSchema();
    expect(p['@type']).toBe('Person');
    expect(p['@id']).toBe(PERSON_ID);
    expect(p.name).toBe(AUTHOR.name);
    expect(p.sameAs as string[]).toEqual([AUTHOR.linkedin, AUTHOR.github]);
  });

  it('ContactPage + ProfilePage reference the Person entity', () => {
    expect(contactPageSchema()['@type']).toBe('ContactPage');
    expect(contactPageSchema().mainEntity).toEqual({ '@id': PERSON_ID });
    const profile = profilePageSchema();
    expect(profile['@type']).toBe('ProfilePage');
    // ProfilePage inlines the full Person as its main entity.
    expect((profile.mainEntity as Record<string, unknown>)['@type']).toBe('Person');
  });

  it('WebPage + CollectionPage carry absolute, in-site URLs', () => {
    const wp = webPageSchema({ name: 'X', path: '/x' });
    expect(wp['@type']).toBe('WebPage');
    expect(wp.url).toBe(absoluteUrl('/x'));
    expect(wp.isPartOf).toEqual({ '@id': SITE_ID });
    const cp = collectionPageSchema({ name: 'Y', path: '/y' });
    expect(cp['@type']).toBe('CollectionPage');
    expect(cp.url).toBe(absoluteUrl('/y'));
  });
});

describe('structured data — content schemas', () => {
  it('FAQPage mirrors the provided Q/A pairs', () => {
    const faqs = [
      { q: 'Q1?', a: 'A1.' },
      { q: 'Q2?', a: 'A2.' },
    ];
    const schema = faqPageSchema(faqs);
    expect(schema['@type']).toBe('FAQPage');
    const entities = schema.mainEntity as Array<Record<string, unknown>>;
    expect(entities).toHaveLength(2);
    expect(entities[0].name).toBe('Q1?');
    expect((entities[0].acceptedAnswer as Record<string, unknown>).text).toBe('A1.');
  });

  it('HowTo numbers steps sequentially from 1', () => {
    const schema = howToSchema({
      name: 'How',
      description: 'desc',
      steps: [
        { name: 'One', text: 'first' },
        { name: 'Two', text: 'second' },
      ],
    });
    expect(schema['@type']).toBe('HowTo');
    const steps = schema.step as Array<Record<string, unknown>>;
    expect(steps.map((s) => s.position)).toEqual([1, 2]);
    expect(steps[0].name).toBe('One');
  });

  it('BreadcrumbList numbers items and uses absolute URLs', () => {
    const schema = breadcrumbSchema([
      { name: 'Home', path: '/' },
      { name: 'Contact', path: '/contact' },
    ]);
    expect(schema['@type']).toBe('BreadcrumbList');
    const items = schema.itemListElement as Array<Record<string, unknown>>;
    expect(items.map((i) => i.position)).toEqual([1, 2]);
    expect(items[1].item).toBe(absoluteUrl('/contact'));
  });
});

describe('JsonLd component', () => {
  it('escapes "<" to prevent breaking out of the script context', () => {
    const html = renderToStaticMarkup(
      React.createElement(JsonLd, { data: { evil: '</script><script>alert(1)' } })
    );
    expect(html).not.toContain('</script><script>');
    expect(html).toContain('\\u003c');
  });

  it('serializes an array of schemas into one script', () => {
    const html = renderToStaticMarkup(
      React.createElement(JsonLd, { data: [organizationSchema(), websiteSchema()] })
    );
    expect(html).toContain('application/ld+json');
    expect(html).toContain('"@type":"Organization"');
    expect(html).toContain('"@type":"WebSite"');
  });
});

describe('robots.txt', () => {
  const r = robots();
  const rule = Array.isArray(r.rules) ? r.rules[0] : r.rules!;
  const disallow = (rule.disallow as string[]) ?? [];

  it('allows the site root', () => {
    expect(rule.allow).toBe('/');
  });

  it('disallows every private/authenticated surface', () => {
    for (const p of ['/home', '/resumes', '/settings', '/admin', '/builder', '/api', '/login']) {
      expect(disallow).toContain(p);
    }
  });

  it('does not disallow public marketing routes', () => {
    for (const p of ['/connect', '/contact', '/privacy', '/terms']) {
      expect(disallow).not.toContain(p);
    }
  });

  it('advertises the sitemap and canonical host', () => {
    expect(r.sitemap).toBe(`${SITE_URL}/sitemap.xml`);
    expect(r.host).toBe(SITE_URL);
  });
});

describe('sitemap.xml', () => {
  const entries = sitemap();
  const urls = entries.map((e) => e.url);

  it('includes the canonical public routes only', () => {
    expect(urls).toContain(absoluteUrl('/'));
    expect(urls).toContain(absoluteUrl('/resume-tailoring'));
    expect(urls).toContain(absoluteUrl('/connect'));
    expect(urls).toContain(absoluteUrl('/contact'));
    expect(urls).toContain(absoluteUrl('/privacy'));
    expect(urls).toContain(absoluteUrl('/terms'));
  });

  it('never leaks private/authenticated routes', () => {
    for (const e of entries) {
      expect(e.url).not.toMatch(/\/(home|resumes|settings|admin|builder|login|api)(\/|$)/);
    }
  });

  it('uses valid priorities and absolute URLs', () => {
    for (const e of entries) {
      expect(e.url.startsWith(SITE_URL)).toBe(true);
      expect(e.priority).toBeGreaterThanOrEqual(0);
      expect(e.priority).toBeLessThanOrEqual(1);
    }
    // The home page is the top priority.
    const home = entries.find((e) => e.url === absoluteUrl('/'));
    expect(home?.priority).toBe(1);
  });
});

describe('manifest.webmanifest', () => {
  const m = manifest();

  it('carries the brand identity and icons', () => {
    expect(m.name).toContain(SITE_NAME);
    expect(m.short_name).toBe(SITE_NAME);
    expect(m.start_url).toBe('/');
    expect((m.icons ?? []).length).toBeGreaterThan(0);
  });

  it('declares theme + background colors', () => {
    expect(m.theme_color).toMatch(/^#/);
    expect(m.background_color).toMatch(/^#/);
  });
});

describe('keyword architecture', () => {
  it('maps every keyword to exactly one page (no cannibalization)', () => {
    const seen = new Map<string, string>();
    const duplicates: string[] = [];
    for (const [group, words] of Object.entries(KEYWORDS)) {
      for (const w of words) {
        const key = w.toLowerCase();
        if (seen.has(key)) {
          duplicates.push(`"${w}" in both "${seen.get(key)}" and "${group}"`);
        } else {
          seen.set(key, group);
        }
      }
    }
    expect(duplicates).toEqual([]);
  });

  it('every keyword group is non-empty', () => {
    for (const [, words] of Object.entries(KEYWORDS)) {
      expect(words.length).toBeGreaterThan(0);
    }
  });
});

describe('capability landing pages', () => {
  it('the slug registry and the data map agree', () => {
    for (const slug of CAPABILITY_SLUGS) {
      expect(CAPABILITIES[slug]).toBeDefined();
      expect(CAPABILITIES[slug].slug).toBe(slug);
    }
    // No stray capabilities outside the registry.
    expect(Object.keys(CAPABILITIES).sort()).toEqual([...CAPABILITY_SLUGS].sort());
  });

  it('every capability is substantive (not thin) and truthfully structured', () => {
    for (const slug of CAPABILITY_SLUGS) {
      const c = CAPABILITIES[slug];
      expect(c.h1.length).toBeGreaterThan(10);
      expect(c.heroSub.length).toBeGreaterThan(40);
      expect(c.definition.length).toBeGreaterThanOrEqual(1);
      expect(c.definition.join(' ').length).toBeGreaterThan(120);
      expect(c.steps.length).toBeGreaterThanOrEqual(3);
      expect(c.outcomes.length).toBeGreaterThanOrEqual(3);
      expect(c.faqs.length).toBeGreaterThanOrEqual(3);
      // Each FAQ has a real question and answer.
      for (const f of c.faqs) {
        expect(f.q.trim().length).toBeGreaterThan(5);
        expect(f.a.trim().length).toBeGreaterThan(20);
      }
    }
  });

  it('every capability has unique metadata mapped to its own keyword cluster', () => {
    const titles = new Set<string>();
    const descriptions = new Set<string>();
    for (const slug of CAPABILITY_SLUGS) {
      const c = CAPABILITIES[slug];
      // Unique title/description — no duplicate metadata across pages.
      expect(titles.has(c.metaTitle)).toBe(false);
      expect(descriptions.has(c.metaDescription)).toBe(false);
      titles.add(c.metaTitle);
      descriptions.add(c.metaDescription);
      // Description stays within the best-practice SERP length (~160 chars).
      expect(c.metaDescription.length).toBeLessThanOrEqual(160);
      // Keyword cluster exists and is non-empty.
      expect(KEYWORDS[c.keywordGroup]?.length ?? 0).toBeGreaterThan(0);
    }
  });

  it('is fully cross-linked (nav covers every capability — no orphans)', () => {
    const navSlugs = CAPABILITY_NAV.map((n) => n.slug).sort();
    expect(navSlugs).toEqual([...CAPABILITY_SLUGS].sort());
    expect(capabilityPath('resume-tailoring')).toBe('/resume-tailoring');
  });

  it('is present in the sitemap', () => {
    const urls = sitemap().map((e) => e.url);
    for (const slug of CAPABILITY_SLUGS) {
      expect(urls).toContain(absoluteUrl(`/${slug}`));
    }
  });
});
