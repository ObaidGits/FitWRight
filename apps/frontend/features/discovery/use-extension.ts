'use client';

/**
 * React access to the companion extension.
 *
 * Detection is done once on mount and re-tried when the tab regains focus,
 * because the usual sequence is: user reads "extension required", installs it,
 * comes back to this tab. Without the refocus retry they would have to reload to
 * be noticed.
 */
import { useCallback, useEffect, useState } from 'react';

import {
  detectExtension,
  requestExtensionScrape,
  type ExtensionCapabilities,
  type ExtensionScrapeResult,
} from './extension-bridge';

export interface UseExtensionResult {
  /** null while detecting, then the capabilities or false when absent. */
  capabilities: ExtensionCapabilities | null;
  detecting: boolean;
  installed: boolean;
  /** Scrape in flight, with the boards it covers. */
  scraping: boolean;
  lastResult: ExtensionScrapeResult | null;
  error: string | null;
  scrape: (request: {
    sites: string[];
    query: string;
    location?: string;
  }) => Promise<ExtensionScrapeResult | null>;
  recheck: () => void;
}

export function useExtension(): UseExtensionResult {
  const [capabilities, setCapabilities] = useState<ExtensionCapabilities | null>(null);
  const [detecting, setDetecting] = useState(true);
  const [scraping, setScraping] = useState(false);
  const [lastResult, setLastResult] = useState<ExtensionScrapeResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const detect = useCallback(async () => {
    setDetecting(true);
    const found = await detectExtension();
    setCapabilities(found);
    setDetecting(false);
  }, []);

  useEffect(() => {
    void detect();

    function onFocus() {
      // Cheap: resolves from the DOM marker when present.
      void detect();
    }
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [detect]);

  const scrape = useCallback(
    async (request: { sites: string[]; query: string; location?: string }) => {
      setScraping(true);
      setError(null);
      const result = await requestExtensionScrape(request);
      setScraping(false);

      if (!result.ok) {
        setError(result.error);
        return null;
      }
      setLastResult(result.data);

      // Only surface a per-board problem when the board yielded nothing at all.
      // Harvesting rows that are already in the feed is a normal repeat search,
      // not an error worth showing.
      const failures = result.data.perSite.filter((s) => s.error && s.found === 0);
      if (failures.length) {
        setError(failures.map((f) => `${f.source}: ${f.error}`).join(' · '));
      }
      return result.data;
    },
    [],
  );

  return {
    capabilities,
    detecting,
    installed: capabilities !== null,
    scraping,
    lastResult,
    error,
    scrape,
    recheck: () => void detect(),
  };
}
