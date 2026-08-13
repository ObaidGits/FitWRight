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
import ShieldCheck from 'lucide-react/dist/esm/icons/shield-check';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { useExtension } from '@/features/discovery/use-extension';
import { apiFetch } from '@/lib/api/client';

/**
 * Searches allowed per board per day. Mirrors `DEFAULT_DAILY_CAP` in
 * `apps/extension/src/lib/pacing.ts`, which is the value actually enforced -
 * this copy exists only because the page must explain the limit before the
 * extension is installed and able to report anything.
 */
const DEFAULT_DAILY_CAP = 6;

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

      {/* The promise that matters most, stated before the instructions rather
          than after them. It is the answer to "what is this thing going to do
          in my browser", and it is also the strongest argument for trusting it
          at all - so burying it under a numbered list would be a mistake. */}
      <Card className="space-y-2 border-[var(--at-success)]/40 bg-[var(--at-success)]/8 p-5">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <ShieldCheck className="h-4 w-4 text-[var(--at-success)]" aria-hidden="true" />
          It never submits an application
        </h2>
        <p className="text-sm text-[var(--muted-foreground)]">
          The extension fills the form and stops. Nothing is sent until you have read it and
          pressed the employer&apos;s own submit button yourself. There is no setting that changes
          this.
        </p>
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

      <Card className="space-y-3 p-5">
        <h2 className="text-sm font-semibold">How it treats the job sites</h2>
        <p className="text-sm text-[var(--muted-foreground)]">
          <span className="font-medium text-[var(--foreground)]">
            It uses your browser and your own accounts.
          </span>{' '}
          Searches run in tabs on your machine, signed in as you — the same pages you could open by
          hand. Nothing is collected on a server on your behalf, and your logins never leave your
          browser.
        </p>
        <p className="text-sm text-[var(--muted-foreground)]">
          <span className="font-medium text-[var(--foreground)]">
            It is paced to keep your accounts safe.
          </span>{' '}
          Each board is searched at most {DEFAULT_DAILY_CAP} times a day, with randomised gaps
          between requests. Sites like LinkedIn and Naukri watch for automated activity, and a
          restricted account would cost you far more than a missed listing — so the limit cannot be
          switched off, only lowered.
        </p>
        <p className="text-sm text-[var(--muted-foreground)]">
          <span className="font-medium text-[var(--foreground)]">
            Most job boards&apos; terms prohibit automated collection.
          </span>{' '}
          Running as you, in your browser, at human pace, is what keeps this reasonable — but it is
          your account and your call. If a site matters to you, read its terms and skip it here if
          you would rather not.
        </p>
      </Card>

      <Card className="space-y-2 p-5">
        <h2 className="text-sm font-semibold">One thing to remember</h2>
        <p className="text-sm text-[var(--muted-foreground)]">
          <span className="font-medium text-[var(--foreground)]">After an update, reload it.</span>{' '}
          Open <code className="rounded bg-[var(--secondary)] px-1">chrome://extensions</code> and
          press Reload on the FitWright card, or new features stay inactive on pages already open.
        </p>
      </Card>

      <ForgetExtensionData />
    </div>
  );
}

/**
 * Delete what the extension contributed.
 *
 * Uninstalling an extension removes the extension; everything it sent stays on the
 * server. "I changed my mind about this feature" deserved a better answer than
 * editing a database, and a feature that collects data should be able to give it
 * back.
 *
 * Two-step, because it cannot be undone - but the confirmation names what will go
 * rather than asking "are you sure", which is a question nobody reads.
 */
function ForgetExtensionData() {
  const [confirming, setConfirming] = React.useState(false);
  const [result, setResult] = React.useState<string | null>(null);
  const [working, setWorking] = React.useState(false);

  async function forget() {
    setWorking(true);
    try {
      const res = await apiFetch('/discovery/data', { method: 'DELETE' });
      if (!res.ok) throw new Error(`Failed: ${res.status}`);
      const data = (await res.json()) as {
        captured_jobs: number;
        learned_answers: number;
        board_health: number;
      };
      setResult(
        `Removed ${data.captured_jobs} captured job${data.captured_jobs === 1 ? '' : 's'}, ` +
          `${data.learned_answers} learned answer${data.learned_answers === 1 ? '' : 's'} and ` +
          `${data.board_health} board record${data.board_health === 1 ? '' : 's'}.`,
      );
    } catch (err) {
      setResult(err instanceof Error ? err.message : 'Could not delete it.');
    } finally {
      setWorking(false);
      setConfirming(false);
    }
  }

  return (
    <Card className="space-y-2 p-5">
      <h2 className="text-sm font-semibold">Remove what the extension collected</h2>
      <p className="text-sm text-[var(--muted-foreground)]">
        Deletes jobs the extension captured that you never acted on, the questions it learned from
        forms, and its record of which boards work. Your applications, resumes and profile are your
        own work and are left alone.
      </p>
      {result && <p className="text-sm text-[var(--at-success)]">{result}</p>}
      {confirming ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => void forget()} disabled={working}>
            {working ? 'Removing…' : 'Yes, remove it'}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
            Keep it
          </Button>
        </div>
      ) : (
        <Button size="sm" variant="outline" onClick={() => setConfirming(true)}>
          Remove extension data
        </Button>
      )}
    </Card>
  );
}
