/** Public marketing route group (Task 4). Atelier-scoped, top-bar chrome. */
import { PublicTopBar } from '@/components/layout/public-top-bar';

export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="atelier min-h-screen bg-[var(--background)] text-[var(--foreground)]">
      <PublicTopBar />
      {children}
    </div>
  );
}
