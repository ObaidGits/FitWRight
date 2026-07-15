import type { Metadata } from 'next';
import { Geist, Space_Grotesk } from 'next/font/google';
import './(default)/css/globals.css';
import { ThemeScript } from '@/components/theme/theme-provider';
import { AppProviders } from '@/components/providers/app-providers';
import { getServerSession } from '@/lib/api/session-server';
import {
  SITE_URL,
  SITE_NAME,
  SITE_DESCRIPTION,
  BRAND_KEYWORDS,
  AUTHOR,
  VERIFICATION,
  OG_IMAGE,
  TWITTER_IMAGE,
} from '@/lib/seo/config';
import { JsonLd } from '@/lib/seo/json-ld';
import { organizationSchema, websiteSchema, personSchema } from '@/lib/seo/structured-data';

/** Only emit a `verification` block when at least one token is configured. */
function buildVerification(): Metadata['verification'] | undefined {
  const { google, bing, yandex } = VERIFICATION;
  if (!google && !bing && !yandex) return undefined;
  return {
    google,
    yandex,
    // Bing uses a custom `msvalidate.01` meta name.
    other: bing ? { 'msvalidate.01': bing } : undefined,
  };
}

const spaceGrotesk = Space_Grotesk({
  variable: '--font-space-grotesk',
  subsets: ['latin'],
  display: 'swap',
});

const geist = Geist({
  variable: '--font-geist',
  subsets: ['latin'],
  display: 'swap',
});

export const metadata: Metadata = {
  // metadataBase makes every relative canonical/OG/Twitter URL resolve to an
  // absolute, environment-correct URL — the foundation for correct indexing.
  metadataBase: new URL(SITE_URL),
  title: {
    default: `${SITE_NAME} — AI Resume Builder & Tailor`,
    template: `%s · ${SITE_NAME}`,
  },
  description: SITE_DESCRIPTION,
  applicationName: SITE_NAME,
  keywords: [...BRAND_KEYWORDS],
  authors: [{ name: AUTHOR.name, url: AUTHOR.url }],
  creator: AUTHOR.name,
  publisher: SITE_NAME,
  category: 'technology',
  alternates: { canonical: '/' },
  // Explicit, generous crawl directives for Google/Bing + AI search bots.
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      'max-video-preview': -1,
      'max-image-preview': 'large',
      'max-snippet': -1,
    },
  },
  icons: {
    icon: '/icon.svg',
  },
  verification: buildVerification(),
  openGraph: {
    title: `${SITE_NAME} — AI Resume Builder & Tailor`,
    description: SITE_DESCRIPTION,
    siteName: SITE_NAME,
    url: '/',
    type: 'website',
    locale: 'en_US',
    images: [OG_IMAGE],
  },
  twitter: {
    card: 'summary_large_image',
    title: `${SITE_NAME} — AI Resume Builder & Tailor`,
    description: SITE_DESCRIPTION,
    images: [TWITTER_IMAGE],
  },
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // SSR-resolved session seeds the provider so there's no unauthenticated flash
  // and no first-paint round-trip. In SINGLE_USER_MODE this returns the owner
  // synchronously without touching cookies (keeps local pages statically
  // renderable); hosted forwards the request cookies to the backend.
  const initialUser = await getServerSession();
  return (
    <html lang="en-US" className="h-full" suppressHydrationWarning>
      <head>
        <ThemeScript />
      </head>
      <body className={`${geist.variable} ${spaceGrotesk.variable} antialiased min-h-full`}>
        {/* Site-wide entity graph (Organization ⇄ WebSite ⇄ Person/founder)
            for rich results and AI retrieval. Page-level schemas reference
            these nodes by @id, so the founder identity resolves on every page. */}
        <JsonLd data={[organizationSchema(), websiteSchema(), personSchema()]} />
        <AppProviders initialUser={initialUser}>{children}</AppProviders>
      </body>
    </html>
  );
}
