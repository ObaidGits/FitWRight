import type { Metadata } from 'next';
import { Geist, Space_Grotesk } from 'next/font/google';
import './(default)/css/globals.css';
import { ThemeScript } from '@/components/theme/theme-provider';
import { AppProviders } from '@/components/providers/app-providers';
import { getServerSession } from '@/lib/api/session-server';

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
  title: 'FitWright',
  description: 'Built to fit. Tailor your resume to every job with FitWright.',
  applicationName: 'FitWright',
  keywords: ['resume', 'fit', 'tailor', 'job', 'application'],
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
      <body
        className={`${geist.variable} ${spaceGrotesk.variable} antialiased bg-background text-ink-soft min-h-full`}
      >
        <AppProviders initialUser={initialUser}>{children}</AppProviders>
      </body>
    </html>
  );
}
