'use client';

/**
 * Job Discovery — redesigned to match FitWright's Atelier design language.
 * Clean, warm, minimal. No show/hide toggles. JD properly formatted.
 */

import * as React from 'react';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Search from 'lucide-react/dist/esm/icons/search';
import Briefcase from 'lucide-react/dist/esm/icons/briefcase';
import MapPin from 'lucide-react/dist/esm/icons/map-pin';
import ExternalLink from 'lucide-react/dist/esm/icons/external-link';
import Wand from 'lucide-react/dist/esm/icons/wand-sparkles';
import RefreshCw from 'lucide-react/dist/esm/icons/refresh-cw';
import Loader2 from 'lucide-react/dist/esm/icons/loader-circle';
import Bell from 'lucide-react/dist/esm/icons/bell';
import Clock from 'lucide-react/dist/esm/icons/clock';
import X from 'lucide-react/dist/esm/icons/x';
import Heart from 'lucide-react/dist/esm/icons/heart';
import ThumbsDown from 'lucide-react/dist/esm/icons/thumbs-down';
import CheckCircle from 'lucide-react/dist/esm/icons/check-circle-2';
import Upload from 'lucide-react/dist/esm/icons/upload';
import Puzzle from 'lucide-react/dist/esm/icons/puzzle';
import Link from 'next/link';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { EmptyState, LoadingSkeleton, ErrorState } from '@/components/atelier/states';
import { Input } from '@/components/atelier/input';
import { useToast } from '@/components/atelier/toast';

import {
  useDiscoveryFeed,
  useRecommendations,
  useTailorForJob,
  useEnableSchedule,
  useManualSearch,
  useUpdateResultStatus,
} from '@/features/discovery/hooks';
import { useTailorResumes } from '@/features/tailor/hooks';
import { useExtension } from '@/features/discovery/use-extension';
import type { FeedResult } from '@/lib/api/discovery';

/**
 * Every board the Discovery page can search, and which lane serves it.
 *
 * `server` boards are scraped by the backend. `extension` boards cannot be
 * reached from a server at all - Cloudflare, an Akamai WAF, a recaptcha, a login
 * wall, or client-side rendering - so those are handed to the companion
 * extension, which runs in the user's own signed-in browser. Each one was
 * confirmed to fail server-side and succeed in a real browser.
 */
const PLATFORMS: { id: string; label: string; lane: 'server' | 'extension' }[] = [
  { id: 'indeed', label: 'Indeed', lane: 'server' },
  { id: 'linkedin', label: 'LinkedIn', lane: 'server' },
  { id: 'zip_recruiter', label: 'ZipRecruiter', lane: 'extension' },
  { id: 'glassdoor', label: 'Glassdoor', lane: 'extension' },
  { id: 'google', label: 'Google', lane: 'extension' },
  { id: 'naukri', label: 'Naukri', lane: 'extension' },
  { id: 'remotive', label: 'Remotive', lane: 'server' },
  { id: 'weworkremotely', label: 'We Work Remotely', lane: 'server' },
  { id: 'simplyhired', label: 'SimplyHired', lane: 'server' },
  { id: 'hirist', label: 'Hirist', lane: 'extension' },
  { id: 'foundit', label: 'Foundit', lane: 'extension' },
  { id: 'wellfound', label: 'Wellfound', lane: 'server' },
  { id: 'ycombinator', label: 'YC Startups', lane: 'extension' },
  { id: 'instahyre', label: 'Instahyre', lane: 'extension' },
];

/** Feed recency windows, in hours, with the wording the summary row uses. */
const RECENCY_LABELS: Record<number, string> = {
  24: 'last 24 hours',
  72: 'last 3 days',
  168: 'last week',
};

const EXTENSION_LANE = new Set(
  PLATFORMS.filter((p) => p.lane === 'extension').map((p) => p.id),
);

export default function DiscoveryPage() {
  const router = useRouter();
  const { data: resumes, isLoading: resumesLoading } = useTailorResumes();

  // Mode & search
  const [useResume, setUseResume] = useState(false);
  const [queryText, setQueryText] = useState('');
  const [location, setLocation] = useState('');
  const [isRemote, setIsRemote] = useState(false);
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>(['indeed']);
  const [hoursOld, setHoursOld] = useState('');
  const [resultsWanted, setResultsWanted] = useState(30);
  const [jobType, setJobType] = useState('');
  const [countryIndeed, setCountryIndeed] = useState('');
  const [resumeId, setResumeId] = useState<string | null>(null);

  // Feed
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [feedLimit, setFeedLimit] = useState(20);

  /**
   * Filters that describe what the list shows, as opposed to what a search
   * fetches.
   *
   * The platform selection applies immediately - selecting Hirist and
   * deselecting LinkedIn must stop showing LinkedIn rows, because the feed is a
   * store of everything ever found, not the last search's output. The text,
   * location and remote filters are snapshotted instead, on Search or Apply, so
   * that typing does not make rows vanish mid-keystroke.
   */
  const [appliedQuery, setAppliedQuery] = useState('');
  const [appliedLocation, setAppliedLocation] = useState('');
  const [appliedRemote, setAppliedRemote] = useState(false);
  // Feed-only filters: they narrow what you already have rather than changing
  // what gets searched, so they apply immediately instead of waiting for
  // "Filter feed". 0 means "no floor" / "any age".
  const [minScore, setMinScore] = useState(0);
  const [postedWithinHours, setPostedWithinHours] = useState(0);

  const feed = useDiscoveryFeed({
    status: statusFilter,
    sources: selectedPlatforms,
    q: appliedQuery,
    location: appliedLocation,
    isRemote: appliedRemote,
    minScore: minScore || undefined,
    postedWithinHours: postedWithinHours || undefined,
    limit: feedLimit,
  });

  // Detail panel
  const [selectedResult, setSelectedResult] = useState<FeedResult | null>(null);

  // Mutations
  const manualSearch = useManualSearch();
  const recommendations = useRecommendations({
    resumeId,
    filters: { location: location || null, is_remote: isRemote || null },
    enabled: false,
  });
  const tailor = useTailorForJob();
  const enableSchedule = useEnableSchedule();
  const updateStatus = useUpdateResultStatus();
  const { toast } = useToast();
  const [tailoringFp, setTailoringFp] = useState<string | null>(null);

  // Companion extension: serves the boards the backend cannot reach.
  const extension = useExtension();
  const selectedExtensionSites = selectedPlatforms.filter((id) => EXTENSION_LANE.has(id));

  const isSearching = manualSearch.isPending || recommendations.isFetching || extension.scraping;
  // Server-filtered: `feedResults` and `feedTotal` already describe the same
  // set, so counts and pagination agree with what is on screen.
  const feedResults = feed.data?.results ?? [];
  const feedTotal = feed.data?.total ?? 0;
  // Jobs carrying a real match score. Gates the match filter: see the control.
  const scoredCount = feed.data?.scored ?? 0;
  const hasFeed = feedTotal > 0;

  /** Filters currently narrowing the list, for the summary row. */
  const activeFilters = [
    selectedPlatforms.length && selectedPlatforms.length < PLATFORMS.length
      ? `${selectedPlatforms.length} platform${selectedPlatforms.length === 1 ? '' : 's'}`
      : null,
    appliedQuery ? `"${appliedQuery}"` : null,
    appliedLocation || null,
    appliedRemote ? 'remote only' : null,
    minScore ? `${minScore}%+ match` : null,
    postedWithinHours ? RECENCY_LABELS[postedWithinHours] : null,
  ].filter(Boolean) as string[];

  /** Snapshot the text/location/remote inputs into the list filters. */
  function applyFeedFilters() {
    setAppliedQuery(queryText.trim());
    setAppliedLocation(location.trim());
    setAppliedRemote(isRemote);
    setFeedLimit(20); // a new filter set starts at page one
  }

  function clearFeedFilters() {
    setAppliedQuery('');
    setAppliedLocation('');
    setAppliedRemote(false);
    setMinScore(0);
    setPostedWithinHours(0);
    setSelectedPlatforms(PLATFORMS.map((p) => p.id));
    setFeedLimit(20);
  }

  function handleSearch() {
    if (useResume && resumeId) {
      recommendations.refetch();
      return;
    }
    if (!queryText.trim()) return;

    // Searching is also a statement of intent about what to look at, so the list
    // filters follow the search: run "designer" and the list stops showing the
    // "python developer" rows from the previous run.
    applyFeedFilters();

    // Split the request by lane. Sending an extension-only board to the backend
    // would just produce a recorded source failure, so those are routed to the
    // extension instead and the two lanes run in parallel.
    const serverSites = selectedPlatforms.filter((id) => !EXTENSION_LANE.has(id));
    const extensionSites = selectedPlatforms.filter((id) => EXTENSION_LANE.has(id));

    if (extensionSites.length && extension.installed) {
      void extension
        .scrape({
          sites: extensionSites,
          query: queryText.trim(),
          location: location || undefined,
        })
        .then((result) => {
          // Refetch on any harvest: even when every row was a duplicate, the
          // feed's ordering and seen-state may have moved.
          if (result && result.total > 0) void feed.refetch();
        });
    }

    if (serverSites.length || !extensionSites.length) {
      manualSearch.mutate(
        {
          query: queryText.trim(),
          location: location || null,
          is_remote: isRemote || null,
          hours_old: hoursOld ? parseInt(hoursOld) : null,
          results_wanted: resultsWanted,
          sites: serverSites.length > 0 ? serverSites : null,
          job_type: jobType || null,
          country_indeed: countryIndeed || null,
          resume_id: useResume ? resumeId : null,
        },
        { onSuccess: () => void feed.refetch() }
      );
    }
  }

  function togglePlatform(id: string) {
    setSelectedPlatforms((prev) =>
      prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]
    );
  }

  function handleTailor(result: FeedResult) {
    const rid = resumeId || resumes?.[0]?.resume_id;
    if (!rid) return;
    setTailoringFp(result.fingerprint);
    tailor.mutate(
      {
        resume_id: rid,
        listing: {
          source: result.source,
          title: result.title,
          company: result.company,
          location: result.location,
          url: result.url,
          is_remote: result.is_remote,
          description: result.description,
          salary: result.salary,
          posted_at: result.posted_at,
          fingerprint: result.fingerprint,
        },
      },
      {
        onSuccess: (r) => router.push(`/tailor?resume=${r.resume_id}&job=${r.job_id}`),
        onSettled: () => setTailoringFp(null),
      }
    );
  }

  function handleStatusChange(id: string, status: string) {
    updateStatus.mutate(
      { id, status },
      {
        onSuccess: (data) => {
          // Saving a job now also queues it. Saying so is what connects the two
          // halves of the product in the user's head - otherwise they open the
          // queue later with no idea why anything is in it.
          if (data.queued) {
            toast({
              title: 'Saved and added to your apply queue',
              description: 'Open Applications → Queue to work through them in order.',
            });
          } else if (status === 'interested') {
            // The one reason queuing can fail is having no resume yet, and that
            // is worth saying out loud rather than silently saving less.
            toast({
              title: 'Saved to your feed',
              description: 'Upload a resume to start queuing jobs to apply to.',
            });
          }
        },
      },
    );
    if (selectedResult?.id === id) setSelectedResult({ ...selectedResult, status });
  }

  const canSearch = useResume ? Boolean(resumeId) : Boolean(queryText.trim());

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6">
      <div className="flex gap-6">
        {/* Left: search + feed */}
        <div className={`min-w-0 flex-1 space-y-5 ${selectedResult ? 'max-w-[55%]' : ''}`}>
          {/* Header */}
          <div className="flex items-end justify-between">
            <div>
              <h1 className="text-xl font-semibold text-[var(--foreground)]">Discover</h1>
              <p className="text-sm text-[var(--muted-foreground)]">
                {hasFeed
                  ? `${feedTotal} ${activeFilters.length ? 'matching' : ''} opportunit${feedTotal === 1 ? 'y' : 'ies'}`.replace(
                      '  ',
                      ' ',
                    )
                  : 'Find your next role'}
              </p>
            </div>
            {hasFeed && (
              <button
                onClick={() => void feed.refetch()}
                disabled={feed.isFetching}
                className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${feed.isFetching ? 'animate-spin' : ''}`} />
                Refresh
              </button>
            )}
          </div>

          {/* Search panel */}
          <div className="space-y-3 rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-4">
            {/* Mode switch */}
            <div className="flex gap-1 rounded-[var(--radius-at-md)] bg-[var(--muted)] p-0.5">
              <button
                onClick={() => setUseResume(false)}
                className={`flex-1 rounded-[var(--radius-at-sm)] px-3 py-1.5 text-xs font-medium transition-all ${
                  !useResume
                    ? 'bg-[var(--card)] text-[var(--foreground)] shadow-sm'
                    : 'text-[var(--muted-foreground)]'
                }`}
              >
                Search by Role
              </button>
              <button
                onClick={() => setUseResume(true)}
                className={`flex-1 rounded-[var(--radius-at-sm)] px-3 py-1.5 text-xs font-medium transition-all ${
                  useResume
                    ? 'bg-[var(--card)] text-[var(--foreground)] shadow-sm'
                    : 'text-[var(--muted-foreground)]'
                }`}
              >
                Match Resume
              </button>
            </div>

            {/* Search input */}
            {!useResume ? (
              <div className="flex gap-2">
                <Input
                  placeholder="Job title, e.g. Backend Engineer"
                  value={queryText}
                  onChange={(e) => setQueryText(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && canSearch && handleSearch()}
                  className="flex-1"
                />
                <Button onClick={handleSearch} disabled={!canSearch || isSearching}>
                  {isSearching ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Search className="h-4 w-4" />
                  )}
                </Button>
              </div>
            ) : (
              <div className="flex gap-2">
                {resumesLoading ? (
                  <div className="h-9 flex-1 animate-pulse rounded-[var(--radius-at-md)] bg-[var(--muted)]" />
                ) : resumes?.length ? (
                  <select
                    className="flex-1 rounded-[var(--radius-at-md)] border border-[var(--input)] bg-[var(--background)] px-3 py-2 text-sm text-[var(--foreground)]"
                    value={resumeId ?? ''}
                    onChange={(e) => setResumeId(e.target.value || null)}
                  >
                    <option value="">Select resume…</option>
                    {resumes.map((r) => (
                      <option key={r.resume_id} value={r.resume_id}>
                        {r.title || r.filename || 'Untitled'}
                      </option>
                    ))}
                  </select>
                ) : (
                  <div className="flex flex-1 items-center gap-2 rounded-[var(--radius-at-md)] border border-dashed border-[var(--border)] p-3 text-sm text-[var(--muted-foreground)]">
                    <Upload className="h-4 w-4" />
                    <Link href="/resumes" className="text-[var(--primary)] hover:underline">
                      Upload a resume first
                    </Link>
                  </div>
                )}
                <Button onClick={handleSearch} disabled={!canSearch || isSearching}>
                  {isSearching ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Search className="h-4 w-4" />
                  )}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    const rid = resumeId || resumes?.[0]?.resume_id;
                    if (rid) enableSchedule.mutate({ resumeId: rid });
                  }}
                  disabled={!resumeId && !resumes?.[0]}
                  title="Auto-discover daily"
                >
                  <Bell className="h-4 w-4" />
                </Button>
              </div>
            )}

            {/* Filters — always visible, no toggle needed */}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
              {/* Location */}
              <div className="flex items-center gap-1.5 text-[var(--muted-foreground)]">
                <MapPin className="h-3.5 w-3.5" />
                <input
                  placeholder="Location"
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                  className="w-28 border-b border-[var(--border)] bg-transparent py-0.5 text-xs text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)] focus:border-[var(--primary)]"
                />
              </div>
              {/* Remote */}
              <label className="flex items-center gap-1.5 text-[var(--muted-foreground)]">
                <input
                  type="checkbox"
                  checked={isRemote}
                  onChange={(e) => setIsRemote(e.target.checked)}
                  className="rounded-sm border-[var(--input)] accent-[var(--primary)]"
                />
                Remote
              </label>
              {/* Country */}
              <select
                value={countryIndeed}
                onChange={(e) => setCountryIndeed(e.target.value)}
                className="border-b border-[var(--border)] bg-transparent py-0.5 text-xs text-[var(--foreground)] outline-none"
              >
                <option value="">Any country</option>
                <option value="usa">USA</option>
                <option value="india">India</option>
                <option value="uk">UK</option>
                <option value="canada">Canada</option>
                <option value="australia">Australia</option>
                <option value="germany">Germany</option>
              </select>
              {/* Date */}
              <select
                value={hoursOld}
                onChange={(e) => setHoursOld(e.target.value)}
                className="border-b border-[var(--border)] bg-transparent py-0.5 text-xs text-[var(--foreground)] outline-none"
              >
                <option value="">Any time</option>
                <option value="24">24h</option>
                <option value="168">Week</option>
                <option value="720">Month</option>
              </select>
              {/* Job Type */}
              <select
                value={jobType}
                onChange={(e) => setJobType(e.target.value)}
                className="border-b border-[var(--border)] bg-transparent py-0.5 text-xs text-[var(--foreground)] outline-none"
              >
                <option value="">Any type</option>
                <option value="fulltime">Full-time</option>
                <option value="parttime">Part-time</option>
                <option value="contract">Contract</option>
                <option value="internship">Internship</option>
              </select>
              {/* Results */}
              <span className="text-[var(--muted-foreground)]">
                {resultsWanted} results
                <input
                  type="range"
                  min={10}
                  max={100}
                  step={10}
                  value={resultsWanted}
                  onChange={(e) => setResultsWanted(parseInt(e.target.value))}
                  className="ml-1.5 w-16 align-middle accent-[var(--primary)]"
                />
              </span>
              {/* Apply narrows the list without re-scraping; Search does both. */}
              {hasFeed && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={applyFeedFilters}
                  title="Filter the jobs already in your feed. Search fetches new ones."
                  className="ml-auto h-6 px-2.5 text-[10px]"
                >
                  Filter feed
                </Button>
              )}
              {activeFilters.length > 0 && (
                <button onClick={clearFeedFilters} className="text-[10px] text-[var(--primary)] hover:underline">
                  Clear
                </button>
              )}
            </div>

            {/* Platforms — always visible */}
            {!useResume && (
              <div className="space-y-2">
                <div className="flex flex-wrap gap-1.5">
                  {PLATFORMS.map((p) => {
                    const selected = selectedPlatforms.includes(p.id);
                    const viaExtension = p.lane === 'extension';
                    return (
                      <button
                        key={p.id}
                        onClick={() => togglePlatform(p.id)}
                        aria-pressed={selected}
                        title={
                          viaExtension
                            ? `${p.label} is searched by the FitWright browser extension`
                            : undefined
                        }
                        className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium transition-all ${
                          selected
                            ? 'bg-[var(--primary)] text-[var(--primary-foreground)]'
                            : 'bg-[var(--muted)] text-[var(--muted-foreground)] hover:bg-[var(--accent)]'
                        }`}
                      >
                        {p.label}
                        {viaExtension && (
                          <Puzzle
                            className={`h-2.5 w-2.5 ${
                              extension.installed ? 'opacity-70' : 'opacity-40'
                            }`}
                            aria-hidden="true"
                          />
                        )}
                      </button>
                    );
                  })}
                </div>

                {/* Extension lane status. Only shown once it is relevant. */}
                {selectedExtensionSites.length > 0 && (
                  <p className="flex items-start gap-1.5 text-[11px] text-[var(--muted-foreground)]">
                    <Puzzle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
                    {extension.detecting ? (
                      <span>Checking for the FitWright extension…</span>
                    ) : extension.installed ? (
                      <span>
                        {selectedExtensionSites.length} board
                        {selectedExtensionSites.length === 1 ? '' : 's'} will be searched by the
                        extension (v{extension.capabilities?.version}) in background tabs — results
                        land in your feed.
                      </span>
                    ) : (
                      <span>
                        <strong className="font-medium text-[var(--foreground)]">
                          Extension required.
                        </strong>{' '}
                        Hirist, Foundit, YC and Instahyre block server-side scraping, so they are
                        searched from your own browser.{' '}
                        <Link
                          href="/setup/extension"
                          className="font-medium text-[var(--primary)] hover:underline"
                        >
                          Set up the extension
                        </Link>{' '}
                        — it takes a minute, and this page notices on its own when it is ready.
                      </span>
                    )}
                  </p>
                )}

                {extension.error && (
                  <p className="text-[11px] text-[var(--at-danger)]">{extension.error}</p>
                )}

                {extension.lastResult && !extension.scraping && (
                  <p className="text-[11px] text-[var(--muted-foreground)]">
                    Extension searched{' '}
                    {extension.lastResult.perSite
                      .map((s) => `${s.source} (${s.found} found${s.saved ? `, ${s.saved} new` : ''})`)
                      .join(' · ')}
                    {extension.lastResult.total > 0 && extension.lastResult.saved === 0 && (
                      <> — all already in your feed.</>
                    )}
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Status banner */}
          {enableSchedule.isSuccess && (
            <p className="text-xs text-[var(--at-success)]">
              <Bell className="mr-1 inline h-3 w-3" />
              Auto-discovery enabled — new jobs will appear here daily.
            </p>
          )}
          {manualSearch.isError && (
            <ErrorState
              description="Search failed. Try again."
              onRetry={() => manualSearch.reset()}
            />
          )}

          {/* Loading */}
          {/* Search progress */}
          {isSearching && (
            <div className="flex items-center gap-3 rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] p-4">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--primary)]" />
              <div>
                <p className="text-sm font-medium text-[var(--foreground)]">Searching job boards…</p>
                <p className="text-xs text-[var(--muted-foreground)]">
                  Checking {selectedPlatforms.join(', ') || 'all platforms'}. This may take 10–15 seconds.
                </p>
              </div>
            </div>
          )}

          {/* Tabs. Shown whenever any filter or tab is active too, not only when
              rows exist: hiding them on an empty result stranded the user on a
              tab they could no longer leave. */}
          {!isSearching && (hasFeed || activeFilters.length > 0 || statusFilter) && (
            <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2 border-b border-[var(--border)]">
              <div className="flex gap-4">
                {[
                  { label: 'All', value: undefined },
                  { label: 'New', value: 'new' },
                  { label: 'Saved', value: 'interested' },
                  { label: 'Applied', value: 'applied' },
                ].map((tab) => (
                  <button
                    key={tab.label}
                    onClick={() => setStatusFilter(tab.value)}
                    className={`pb-2 text-xs font-medium transition-colors ${
                      statusFilter === tab.value
                        ? 'border-b-2 border-[var(--primary)] text-[var(--foreground)]'
                        : 'text-[var(--muted-foreground)] hover:text-[var(--foreground)]'
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              {/* Filters on the feed you already have, deliberately next to the
                  status tabs rather than in the search form above: those inputs
                  decide what gets fetched, these decide what gets shown, and
                  mixing the two is what made this page confusing. */}
              <div className="flex flex-wrap items-center gap-3 pb-2">
                {/* Only offered once something has actually been scored.
                    Scores come from matching against a resume; a keyword harvest
                    stores none, so on a fresh feed this control could only ever
                    return an empty list. */}
                {scoredCount > 0 && (
                <label className="flex items-center gap-1.5 text-[11px] text-[var(--muted-foreground)]">
                  Match
                  <select
                    value={minScore}
                    onChange={(e) => {
                      setMinScore(parseInt(e.target.value, 10));
                      setFeedLimit(20);
                    }}
                    className="rounded-[var(--radius-at-sm)] border border-[var(--border)] bg-[var(--card)] px-1.5 py-0.5 text-[11px]"
                  >
                    <option value={0}>any</option>
                    <option value={50}>50%+</option>
                    <option value={70}>70%+</option>
                    <option value={85}>85%+</option>
                  </select>
                </label>
                )}
                <label className="flex items-center gap-1.5 text-[11px] text-[var(--muted-foreground)]">
                  Posted
                  <select
                    value={postedWithinHours}
                    onChange={(e) => {
                      setPostedWithinHours(parseInt(e.target.value, 10));
                      setFeedLimit(20);
                    }}
                    className="rounded-[var(--radius-at-sm)] border border-[var(--border)] bg-[var(--card)] px-1.5 py-0.5 text-[11px]"
                  >
                    <option value={0}>any time</option>
                    <option value={24}>last 24 hours</option>
                    <option value={72}>last 3 days</option>
                    <option value={168}>last week</option>
                  </select>
                </label>
              </div>
            </div>
          )}

          {/* Empty state — distinguishes "nothing yet" from "nothing matches" */}
          {!feed.isLoading && !hasFeed && !isSearching && (
            <EmptyState
              icon={Briefcase}
              title={activeFilters.length ? 'No jobs match these filters' : 'No jobs yet'}
              description={
                activeFilters.length
                  ? `Nothing in your feed matches ${activeFilters.join(' · ')}. Press Search to fetch from the selected platforms, or clear the filters to see everything you have.`
                  : 'Search for a role or match your resume to start discovering opportunities.'
              }
            />
          )}
          {!feed.isLoading && !hasFeed && !isSearching && activeFilters.length > 0 && (
            <div className="flex justify-center">
              <Button size="sm" variant="outline" onClick={clearFeedFilters}>
                Clear filters
              </Button>
            </div>
          )}

          {/* What the list is showing, and why. */}
          {hasFeed && !isSearching && (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[var(--muted-foreground)]">
              <span>
                Showing {feedResults.length} of {feedTotal}
              </span>
              {activeFilters.length > 0 && (
                <>
                  <span aria-hidden="true">·</span>
                  <span>filtered by {activeFilters.join(' · ')}</span>
                  <button
                    onClick={clearFeedFilters}
                    className="text-[var(--primary)] hover:underline"
                  >
                    Clear
                  </button>
                </>
              )}
            </div>
          )}

          {/* Feed cards */}
          {hasFeed && !isSearching && (
            <div className="space-y-2">
              {feedResults.map((r) => (
                <button
                  key={r.id}
                  onClick={() => setSelectedResult(r)}
                  className={`w-full rounded-[var(--radius-at-md)] border p-3 text-left transition-all hover:border-[var(--primary)]/30 hover:shadow-sm ${
                    selectedResult?.id === r.id
                      ? 'border-[var(--primary)]/40 bg-[var(--accent)]'
                      : 'border-[var(--border)] bg-[var(--card)]'
                  } ${r.status === 'dismissed' ? 'opacity-40' : ''}`}
                >
                  <div className="flex items-start gap-3">
                    {/* Platform badge */}
                    <PlatformBadge source={r.source} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium text-[var(--foreground)]">
                          {r.title}
                        </span>
                        {!r.seen && (
                          <span className="h-1.5 w-1.5 rounded-full bg-[var(--primary)]" />
                        )}
                      </div>
                      <p className="mt-0.5 truncate text-xs text-[var(--muted-foreground)]">
                        {r.company || 'Company not listed'}
                        {r.location ? ` · ${r.location}` : ''}
                        {r.is_remote ? ' · Remote' : ''}
                        {/* Naming the other boards is what makes collapsing
                            trustworthy: the user can see nothing was hidden. */}
                        {r.also_on?.length ? ` · also on ${r.also_on.join(', ')}` : ''}
                      </p>
                      {/* Salary + posted date row */}
                      <div className="mt-1 flex items-center gap-2 text-[10px]">
                        {r.salary && (
                          <span className="font-medium text-[var(--at-success)]">{r.salary}</span>
                        )}
                        {r.posted_at && (
                          <span className="text-[var(--muted-foreground)]">{timeAgo(r.posted_at)}</span>
                        )}
                      </div>
                      {(r.matched_keywords?.length ?? 0) > 0 && (
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {r.matched_keywords!.slice(0, 3).map((kw) => (
                            <span
                              key={kw}
                              className="rounded bg-[var(--at-success)]/10 px-1.5 py-0.5 text-[9px] font-medium text-[var(--at-success)]"
                            >
                              {kw}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    {r.match_score > 0 && (
                      <span className="text-sm font-semibold tabular-nums text-[var(--primary)]">
                        {Math.round(r.match_score)}%
                      </span>
                    )}
                  </div>
                </button>
              ))}
              {/* Count against rows actually loaded, not the page size: with a
                  filter applied the two diverge and the old math offered "show
                  more" when everything was already on screen. */}
              {feedTotal > feedResults.length && (
                <button
                  onClick={() => setFeedLimit((l) => l + 20)}
                  className="w-full py-2 text-xs font-medium text-[var(--primary)] hover:underline"
                >
                  Show more ({feedTotal - feedResults.length} remaining)
                </button>
              )}
            </div>
          )}
        </div>

        {/* Right: Detail panel */}
        {selectedResult && (
          <aside className="sticky top-20 hidden h-fit max-h-[calc(100vh-7rem)] w-[45%] overflow-y-auto rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-5 lg:block">
            {/* Header */}
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-[var(--foreground)]">
                  {selectedResult.title}
                </h2>
                <p className="mt-0.5 text-sm text-[var(--muted-foreground)]">
                  {selectedResult.company} · {selectedResult.location}
                </p>
              </div>
              <button
                onClick={() => setSelectedResult(null)}
                className="rounded-[var(--radius-at-sm)] p-1 text-[var(--muted-foreground)] hover:bg-[var(--accent)]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Meta badges */}
            <div className="mt-3 flex flex-wrap gap-1.5">
              {selectedResult.salary && <Badge variant="success">{selectedResult.salary}</Badge>}
              {selectedResult.is_remote && <Badge variant="neutral">Remote</Badge>}
              {selectedResult.posted_at && (
                <Badge variant="neutral">
                  <Clock className="mr-1 h-3 w-3" />
                  {new Date(selectedResult.posted_at).toLocaleDateString()}
                </Badge>
              )}
              <Badge variant="neutral" className="capitalize">
                {selectedResult.source}
              </Badge>
              {selectedResult.match_score > 0 && (
                <Badge variant="primary">{Math.round(selectedResult.match_score)}% match</Badge>
              )}
            </div>

            {/* Actions */}
            <div className="mt-4 flex gap-2">
              <Button
                size="sm"
                variant={selectedResult.status === 'interested' ? 'primary' : 'outline'}
                onClick={() => handleStatusChange(selectedResult.id, 'interested')}
              >
                <Heart className="mr-1 h-3.5 w-3.5" /> Save
              </Button>
              <Button
                size="sm"
                onClick={() => handleTailor(selectedResult)}
                disabled={tailoringFp === selectedResult.fingerprint}
              >
                {tailoringFp === selectedResult.fingerprint ? (
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Wand className="mr-1 h-3.5 w-3.5" />
                )}
                Tailor
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => handleStatusChange(selectedResult.id, 'applied')}
              >
                <CheckCircle className="mr-1 h-3.5 w-3.5" /> Applied
              </Button>
              {/* Dual apply links */}
              <ApplyLinks url={selectedResult.url} source={selectedResult.source} description={selectedResult.description} />
              <Button
                size="sm"
                variant="outline"
                onClick={() => handleStatusChange(selectedResult.id, 'dismissed')}
                className="ml-auto"
              >
                <ThumbsDown className="h-3.5 w-3.5" />
              </Button>
            </div>

            {/* Skill match */}
            {((selectedResult.matched_keywords?.length ?? 0) > 0 ||
              (selectedResult.missing_keywords?.length ?? 0) > 0) && (
              <div className="mt-4 rounded-[var(--radius-at-md)] bg-[var(--at-surface-2)] p-3">
                <p className="mb-2 text-[10px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                  Skill Match
                </p>
                <div className="flex flex-wrap gap-1">
                  {selectedResult.matched_keywords?.map((kw) => (
                    <span
                      key={kw}
                      className="rounded-full bg-[var(--at-success)]/15 px-2 py-0.5 text-[10px] font-medium text-[var(--at-success)]"
                    >
                      ✓ {kw}
                    </span>
                  ))}
                  {selectedResult.missing_keywords?.slice(0, 10).map((kw) => (
                    <span
                      key={kw}
                      className="rounded-full bg-[var(--destructive)]/10 px-2 py-0.5 text-[10px] font-medium text-[var(--destructive)]"
                    >
                      ✗ {kw}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Job Description — properly formatted */}
            <div className="mt-4">
              <p className="mb-2 text-[10px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                Description
              </p>
              <div className="max-h-[50vh] overflow-y-auto rounded-[var(--radius-at-md)] bg-[var(--background)] p-3">
                {selectedResult.description ? (
                  <div
                    className="prose prose-sm max-w-none text-[var(--foreground)] prose-headings:text-[var(--foreground)] prose-strong:text-[var(--foreground)] prose-ul:text-[var(--foreground)]"
                    dangerouslySetInnerHTML={{
                      __html: formatJobDescription(selectedResult.description),
                    }}
                  />
                ) : (
                  <p className="text-sm italic text-[var(--muted-foreground)]">
                    No description available.{' '}
                    <a
                      href={selectedResult.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[var(--primary)] hover:underline"
                    >
                      View on {selectedResult.source}
                    </a>
                  </p>
                )}
              </div>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}

/**
 * Format raw job description text into well-structured HTML.
 * Handles the markdown-like output from JobSpy/Indeed scrapers:
 * - Escaped backslashes (\-, \\-)
 * - Markdown headers (###, ####, **Header at start of line**)
 * - Bold (**text**), italic (*text*)
 * - Bullet lists (*, -, •, +)
 * - Numbered lists (1., 2., etc.)
 * - Horizontal rules (---, ===)
 * - Section separators
 */
function formatJobDescription(text: string): string {
  // Step 1: Clean up scraper artifacts
  let cleaned = text
    // Remove escaped backslashes from markdown (\\- → -, \- → -)
    .replace(/\\\\-/g, '-')
    .replace(/\\-/g, '-')
    .replace(/\\\\/g, '')
    // Normalize line endings
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n');

  // Step 2: Split into lines and process
  const lines = cleaned.split('\n');
  const html: string[] = [];
  let inList = false;
  let listType: 'ul' | 'ol' | null = null;

  for (let i = 0; i < lines.length; i++) {
    let line = lines[i].trim();

    // Skip empty lines (add spacing)
    if (!line) {
      if (inList) {
        html.push(listType === 'ol' ? '</ol>' : '</ul>');
        inList = false;
        listType = null;
      }
      html.push('<div class="h-2"></div>');
      continue;
    }

    // Horizontal rules (--- or ===)
    if (/^[-=]{3,}$/.test(line)) {
      if (inList) { html.push(listType === 'ol' ? '</ol>' : '</ul>'); inList = false; }
      html.push('<hr class="my-3 border-[var(--border)]" />');
      continue;
    }

    // Markdown headers: # ## ### ####
    const headerMatch = line.match(/^(#{1,4})\s+(.+)$/);
    if (headerMatch) {
      if (inList) { html.push(listType === 'ol' ? '</ol>' : '</ul>'); inList = false; }
      const level = headerMatch[1].length;
      const content = escapeAndFormat(headerMatch[2]);
      const sizes = ['text-base font-bold', 'text-sm font-bold', 'text-sm font-semibold', 'text-xs font-semibold uppercase tracking-wide'];
      html.push(`<h${level + 2} class="${sizes[level - 1] || sizes[2]} mt-4 mb-1.5 text-[var(--foreground)]">${content}</h${level + 2}>`);
      continue;
    }

    // Bold-only line as a section header: **Some Header**
    if (/^\*\*(.+)\*\*\s*$/.test(line) && !line.includes('**', line.indexOf('**') + 2 + line.match(/^\*\*(.+?)\*\*/)?.[1]?.length!)) {
      if (inList) { html.push(listType === 'ol' ? '</ol>' : '</ul>'); inList = false; }
      const content = line.replace(/^\*\*(.+)\*\*\s*$/, '$1');
      html.push(`<h4 class="mt-4 mb-1.5 text-sm font-semibold text-[var(--foreground)]">${escapeHtml(content)}</h4>`);
      continue;
    }

    // Bullet list items: *, -, •, +
    const bulletMatch = line.match(/^[\*\-•+]\s+(.+)$/);
    if (bulletMatch) {
      if (!inList || listType !== 'ul') {
        if (inList) html.push(listType === 'ol' ? '</ol>' : '</ul>');
        html.push('<ul class="my-1.5 space-y-1 pl-4">');
        inList = true;
        listType = 'ul';
      }
      html.push(`<li class="text-sm text-[var(--foreground)] leading-relaxed list-disc">${escapeAndFormat(bulletMatch[1])}</li>`);
      continue;
    }

    // Numbered list: 1. 2. etc.
    const numMatch = line.match(/^\d+[.)]\s+(.+)$/);
    if (numMatch) {
      if (!inList || listType !== 'ol') {
        if (inList) html.push(listType === 'ol' ? '</ol>' : '</ul>');
        html.push('<ol class="my-1.5 space-y-1 pl-4 list-decimal">');
        inList = true;
        listType = 'ol';
      }
      html.push(`<li class="text-sm text-[var(--foreground)] leading-relaxed">${escapeAndFormat(numMatch[1])}</li>`);
      continue;
    }

    // Regular paragraph
    if (inList) { html.push(listType === 'ol' ? '</ol>' : '</ul>'); inList = false; listType = null; }
    html.push(`<p class="text-sm text-[var(--foreground)] leading-relaxed">${escapeAndFormat(line)}</p>`);
  }

  // Close any open list
  if (inList) html.push(listType === 'ol' ? '</ol>' : '</ul>');

  return html.join('\n');
}

/** Escape HTML entities. */
function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** Escape HTML + apply inline formatting (bold, italic, links). */
function escapeAndFormat(s: string): string {
  let out = escapeHtml(s);
  // Bold: **text**
  out = out.replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold">$1</strong>');
  // Italic: *text*
  out = out.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // Inline code: `text`
  out = out.replace(/`(.+?)`/g, '<code class="rounded bg-[var(--muted)] px-1 py-0.5 text-xs">$1</code>');
  return out;
}


/** Platform badge with brand color. */
const PLATFORM_COLORS: Record<string, { bg: string; text: string; label: string }> = {
  indeed: { bg: '#003A9B', text: '#fff', label: 'Indeed' },
  linkedin: { bg: '#0A66C2', text: '#fff', label: 'LinkedIn' },
  glassdoor: { bg: '#0CAA41', text: '#fff', label: 'Glassdoor' },
  google: { bg: '#4285F4', text: '#fff', label: 'Google' },
  naukri: { bg: '#4A90D9', text: '#fff', label: 'Naukri' },
  zip_recruiter: { bg: '#5BA94B', text: '#fff', label: 'ZipRecruiter' },
  remotive: { bg: '#4B2AAD', text: '#fff', label: 'Remotive' },
  weworkremotely: { bg: '#1A1A2E', text: '#fff', label: 'WWR' },
  simplyhired: { bg: '#6B4FBB', text: '#fff', label: 'SimplyHired' },
  hirist: { bg: '#FF6B35', text: '#fff', label: 'Hirist' },
  foundit: { bg: '#E84C3D', text: '#fff', label: 'Foundit' },
  wellfound: { bg: '#000', text: '#fff', label: 'Wellfound' },
  ycombinator: { bg: '#F26522', text: '#fff', label: 'YC' },
  instahyre: { bg: '#2196F3', text: '#fff', label: 'Instahyre' },
};

function PlatformBadge({ source }: { source: string }) {
  const platform = PLATFORM_COLORS[source.toLowerCase()] || {
    bg: 'var(--muted)',
    text: 'var(--muted-foreground)',
    label: source,
  };
  return (
    <span
      className="mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider"
      style={{ backgroundColor: platform.bg, color: platform.text }}
    >
      {platform.label}
    </span>
  );
}

/** Relative time display (e.g. "2d ago", "1w ago"). */
function timeAgo(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  const diffWeeks = Math.floor(diffDays / 7);
  if (diffWeeks < 4) return `${diffWeeks}w ago`;
  return `${Math.floor(diffDays / 30)}mo ago`;
}


/** Known job board domains — if the URL is on one of these, it's not a "direct" link. */
const BOARD_DOMAINS = ['indeed.com', 'linkedin.com', 'glassdoor.com', 'naukri.com', 'google.com'];

/** Known ATS/company career page patterns to extract from JD text. */
const ATS_PATTERNS = [
  /https?:\/\/[\w.-]*greenhouse\.io\/[\w\-/]+/gi,
  /https?:\/\/[\w.-]*lever\.co\/[\w\-/]+/gi,
  /https?:\/\/[\w.-]*workday\.com\/[\w\-/]+/gi,
  /https?:\/\/[\w.-]*ashbyhq\.com\/[\w\-/]+/gi,
  /https?:\/\/[\w.-]*smartrecruiters\.com\/[\w\-/]+/gi,
  /https?:\/\/[\w.-]*icims\.com\/[\w\-/]+/gi,
  /https?:\/\/[\w.-]*myworkdayjobs\.com\/[\w\-/]+/gi,
  /https?:\/\/[\w.-]*jobs\.lever\.co\/[\w\-/]+/gi,
  /https?:\/\/careers\.[\w.-]+\/[\w\-/]+/gi,
  /https?:\/\/[\w.-]+\/careers\/[\w\-/]+/gi,
  /https?:\/\/[\w.-]+\/jobs?\/[\w\-/]+/gi,
];

/** Extract a direct apply URL from the job description text. */
function extractDirectUrl(description: string | null | undefined, sourceUrl: string): string | null {
  if (!description) return null;
  // Check if the main URL is already direct (not a board)
  const isBoard = BOARD_DOMAINS.some((d) => sourceUrl.includes(d));
  if (!isBoard) return null; // main URL is already direct, no need for a second link

  // Search description for ATS/career page URLs
  for (const pattern of ATS_PATTERNS) {
    pattern.lastIndex = 0;
    const match = pattern.exec(description);
    if (match) return match[0];
  }
  return null;
}

/** Dual apply links: platform link + direct apply (if available). */
function ApplyLinks({
  url,
  source,
  description,
}: {
  url: string;
  source: string;
  description?: string | null;
}) {
  const directUrl = extractDirectUrl(description, url);
  const platformLabel = PLATFORM_COLORS[source.toLowerCase()]?.label || source;

  return (
    <div className="flex items-center gap-1.5">
      <a href={url} target="_blank" rel="noopener noreferrer">
        <Button size="sm" variant="outline">
          <ExternalLink className="mr-1 h-3.5 w-3.5" />
          {platformLabel}
        </Button>
      </a>
      {directUrl && (
        <a href={directUrl} target="_blank" rel="noopener noreferrer">
          <Button size="sm" variant="primary">
            <ExternalLink className="mr-1 h-3.5 w-3.5" />
            Apply Direct
          </Button>
        </a>
      )}
    </div>
  );
}
