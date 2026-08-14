/**
 * The search progress strip.
 *
 * This component is the reason the background search is not a regression: without
 * visible narration the button would return instantly and then nothing would
 * appear to happen for half a minute.
 */

import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import { SearchProgressBar } from '@/components/discovery/search-progress-bar';
import type { SearchProgress } from '@/lib/api/discovery';

function progress(overrides: Partial<SearchProgress> = {}): SearchProgress {
  return {
    search_id: 'abc',
    status: 'running',
    query: 'engineer',
    sites: ['indeed', 'linkedin'],
    done_sites: [],
    sites_total: 2,
    sites_done: 0,
    found: 0,
    saved: 0,
    failures: [],
    error: null,
    elapsed_ms: 3_000,
    ...overrides,
  };
}

describe('SearchProgressBar', () => {
  it('tells the user roughly how long a running search takes', () => {
    render(<SearchProgressBar progress={progress()} />);
    expect(screen.getByText(/15–30 seconds/)).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
  });

  it('reports jobs found so far once boards start landing', () => {
    render(
      <SearchProgressBar
        progress={progress({ found: 14, sites_done: 1, done_sites: ['indeed'] })}
      />
    );
    expect(screen.getByText(/14 jobs so far/)).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
  });

  it('announces how many jobs reached the feed on success', () => {
    render(<SearchProgressBar progress={progress({ status: 'done', found: 20, saved: 6, sites_done: 2 })} />);
    expect(screen.getByText(/Added 6 jobs to your feed/)).toBeInTheDocument();
  });

  it('distinguishes "nothing new" from "nothing found"', () => {
    render(<SearchProgressBar progress={progress({ status: 'done', found: 12, saved: 0, sites_done: 2 })} />);
    expect(screen.getByText(/already in your feed/)).toBeInTheDocument();

    render(<SearchProgressBar progress={progress({ status: 'done', found: 0, saved: 0, sites_done: 2 })} />);
    expect(screen.getByText(/No jobs matched this search/)).toBeInTheDocument();
  });

  it('names the boards that could not be reached rather than quietly returning less', () => {
    render(
      <SearchProgressBar
        progress={progress({
          status: 'done',
          saved: 3,
          sites_done: 1,
          failures: [{ source: 'linkedin', reason: 'blocked' }],
        })}
      />
    );
    expect(screen.getByText(/could not be reached: linkedin/)).toBeInTheDocument();
  });

  it('offers a feed reload when the server forgot the search', () => {
    const onRefresh = vi.fn();
    render(<SearchProgressBar progress={progress({ status: 'expired' })} onRefresh={onRefresh} />);
    expect(screen.getByText(/Lost track of this search/)).toBeInTheDocument();
    screen.getByRole('button', { name: /Reload feed/i }).click();
    expect(onRefresh).toHaveBeenCalled();
  });

  it('surfaces a failure reason and any jobs saved before it stopped', () => {
    render(
      <SearchProgressBar
        progress={progress({ status: 'failed', error: 'The search failed (TimeoutError).', saved: 2 })}
      />
    );
    expect(screen.getByRole('alert')).toHaveTextContent('TimeoutError');
    expect(screen.getByRole('alert')).toHaveTextContent('2 jobs were saved');
  });
});
