'use client';

/**
 * Browser extension setup.
 *
 * Chrome has no API that installs an unpacked extension, and it removed
 * double-click `.crx` installs years ago - so the four manual steps cannot be
 * automated away. What *can* be removed is every point where a non-technical
 * user has to guess: the page names the exact folder, says whether it has been
 * built, and flips itself to "Connected" the moment the extension answers, so
 * nobody has to wonder whether it worked.
 *
 * Detection re-runs when the tab regains focus, which is exactly the moment the
 * user comes back from chrome://extensions.
 */
import * as React from 'react';
import Check from 'lucide-react/dist/esm/icons/check';
import CircleAlert from 'lucide-react/dist/esm/icons/circle-alert';
import Copy from 'lucide-react/dist/esm/icons/copy';
import Puzzle from 'lucide-react/dist/esm/icons/puzzle';
import RefreshCw from 'lucide-react/dist/esm/icons/refresh-cw';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { useExtension } from '@/features/discovery/use-extension';
import { apiFetch } from '@/lib/api/client';

interface InstallInfo {
  dist_path: string | null;
  built: boolean;
  local: boolean;
}

function useInstallInfo() {
  const [info, setInfo] = React.useState<InstallInfo | null>(null);
  React.useEffect(() => {
    let cancelled = false;
    void apiFetch('/extension/install-info', { method: 'GET' })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled) setInfo(data);
      })
      .catch(() => {
        /* The page is still useful without the path. */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return info;
}

function CopyPath({ path }: { path: string }) {
  const [copied, setCopied] = React.useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(path);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard blocked (no permission, or an insecure origin). The path is
      // on screen and selectable, so this is a convenience, not the mechanism.
    }
  };

  return (
    <div className="flex items-center gap-2 rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--secondary)] p-2">
      <code className="min-w-0 flex-1 break-all text-xs">{path}</code>
      <Button size="sm" variant="outline" onClick={copy}>
        <Copy className="h-3.5 w-3.5" />
        {copied ? 'Copied' : 'Copy'}
      </Button>
    </div>
  );
}

export default function ExtensionSetupPage() {
  const { installed, detecting, capabilities, recheck } = useExtension();
  const info = useInstallInfo();

  return (
    <div className="mx-auto max-w-2xl space-y-6 p-4 md:p-6">
      <header>
        <h1 className="text-xl font-semibold">Browser extension</h1>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          The extension fills application forms for you and reaches job boards a server cannot.
        </p>
      </header>

      {/* Status first: the answer to "did it work?" should never require reading
          instructions the user has already followed. */}
      <Card
        className={`flex items-start gap-3 p-4 ${
          installed
            ? 'border-[var(--at-success)]/40 bg-[var(--at-success)]/8'
            : 'border-[var(--border)]'
        }`}
        aria-live="polite"
      >
        {installed ? (
          <Check className="mt-0.5 h-5 w-5 shrink-0 text-[var(--at-success)]" aria-hidden="true" />
        ) : (
          <Puzzle className="mt-0.5 h-5 w-5 shrink-0 text-[var(--muted-foreground)]" aria-hidden="true" />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">
            {detecting ? 'Looking for the extension…' : installed ? 'Connected' : 'Not detected yet'}
          </p>
          <p className="mt-0.5 text-sm text-[var(--muted-foreground)]">
            {installed
              ? `Version ${capabilities?.version ?? 'unknown'}. Autofill and the extra job boards are available.`
              : 'Follow the steps below. This updates itself as soon as the extension is loaded.'}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={recheck} disabled={detecting}>
          <RefreshCw className="h-3.5 w-3.5" /> Check again
        </Button>
      </Card>

      {!installed && (
        <Card className="space-y-4 p-5">
          <h2 className="text-sm font-semibold">Install it</h2>

          {info?.local && info.dist_path && !info.built && (
            <p className="flex items-start gap-2 text-sm text-[var(--at-warning)]">
              <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              The extension has not been built yet. Run{' '}
              <code className="rounded bg-[var(--secondary)] px-1">npm run build</code> inside{' '}
              <code className="rounded bg-[var(--secondary)] px-1">apps/extension</code> first.
            </p>
          )}

          <ol className="space-y-4 text-sm">
            <li className="space-y-1.5">
              <p className="font-medium">1. Open your extensions page</p>
              <p className="text-[var(--muted-foreground)]">
                Copy <code className="rounded bg-[var(--secondary)] px-1">chrome://extensions</code>{' '}
                into the address bar and press Enter. Browsers block links to that page, so it has to
                be pasted by hand.
              </p>
            </li>
            <li className="space-y-1.5">
              <p className="font-medium">2. Turn on “Developer mode”</p>
              <p className="text-[var(--muted-foreground)]">
                It is a switch in the top-right corner of that page. This is what lets you use an
                extension that is not from the Chrome Web Store.
              </p>
            </li>
            <li className="space-y-1.5">
              <p className="font-medium">3. Click “Load unpacked” and choose this folder</p>
              {info?.local && info.dist_path ? (
                <CopyPath path={info.dist_path} />
              ) : (
                <p className="text-[var(--muted-foreground)]">
                  Select the <code className="rounded bg-[var(--secondary)] px-1">dist</code> folder
                  inside <code className="rounded bg-[var(--secondary)] px-1">apps/extension</code>{' '}
                  in your FitWright folder.
                </p>
              )}
            </li>
            <li className="space-y-1.5">
              <p className="font-medium">4. Come back to this tab</p>
              <p className="text-[var(--muted-foreground)]">
                The banner above turns green on its own. Nothing else to do.
              </p>
            </li>
          </ol>
        </Card>
      )}

      <Card className="space-y-2 p-5">
        <h2 className="text-sm font-semibold">Two things worth knowing</h2>
        <p className="text-sm text-[var(--muted-foreground)]">
          <span className="font-medium text-[var(--foreground)]">It never submits anything.</span>{' '}
          The extension fills a form and stops. You read it and press submit yourself.
        </p>
        <p className="text-sm text-[var(--muted-foreground)]">
          <span className="font-medium text-[var(--foreground)]">After an update, reload it.</span>{' '}
          Open <code className="rounded bg-[var(--secondary)] px-1">chrome://extensions</code> and
          press Reload on the FitWright card, or new features stay inactive on pages already open.
        </p>
      </Card>
    </div>
  );
}
