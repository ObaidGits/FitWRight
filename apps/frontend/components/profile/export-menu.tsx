'use client';

/**
 * Export menu (P6) — download the profile in portable formats.
 *
 * Each format is a pure server-side projection of the single profile document
 * (JSON Resume export round-trips with import; public/portfolio are safe,
 * private-field-free views). Downloads happen client-side from the fetched JSON
 * so no extra endpoint or storage is needed.
 */
import * as React from 'react';
import Share2 from 'lucide-react/dist/esm/icons/share-2';

import { Button } from '@/components/atelier/button';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/atelier/dropdown-menu';
import { useToast } from '@/components/atelier/toast';
import { getProjection } from '@/lib/api/professional-profile';

function download(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function ExportMenu() {
  const { toast } = useToast();
  const [busy, setBusy] = React.useState(false);

  async function exportAs(kind: 'export/json-resume' | 'public' | 'portfolio', filename: string) {
    setBusy(true);
    try {
      const data = await getProjection(kind);
      download(filename, data);
      toast({ title: 'Exported', variant: 'success' });
    } catch (err) {
      toast({
        title: 'Export failed',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" loading={busy}>
          <Share2 className="h-4 w-4" /> Export
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => exportAs('export/json-resume', 'resume.json')}>
          JSON Resume
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => exportAs('public', 'public-profile.json')}>
          Public profile (JSON)
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => exportAs('portfolio', 'portfolio.json')}>
          Portfolio (JSON)
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
