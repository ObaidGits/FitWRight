import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConflictDialog } from '@/components/resilience/conflict-dialog';
import { SaveStatusChip } from '@/components/resilience/save-status-chip';
import { DegradationBanner } from '@/components/resilience/degradation-banner';

describe('ConflictDialog', () => {
  const mine = { summary: 'my summary', name: 'Jane' };
  const latest = { summary: 'server summary', name: 'Jane' };

  it('renders a field-level diff and the three resolution actions', () => {
    render(
      <ConflictDialog
        mine={mine}
        latest={latest}
        base={{ summary: 'base', name: 'Jane' }}
        currentVersion={5}
        onKeepMine={() => {}}
        onTakeLatest={() => {}}
        onMerge={() => {}}
        onDismiss={() => {}}
      />
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/changed elsewhere/i)).toBeInTheDocument();
    // Overlapping change (summary) → merge NOT offered.
    expect(screen.queryByRole('button', { name: /merge/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /keep my changes/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /take the latest/i })).toBeInTheDocument();
  });

  it('offers merge only for disjoint changes and calls back with the merge', () => {
    const onMerge = vi.fn();
    render(
      <ConflictDialog
        mine={{ summary: 'mine', name: 'Jane' }}
        latest={{ summary: 'base', name: 'Janet' }}
        base={{ summary: 'base', name: 'Jane' }}
        currentVersion={5}
        onKeepMine={() => {}}
        onTakeLatest={() => {}}
        onMerge={onMerge}
        onDismiss={() => {}}
      />
    );
    const mergeBtn = screen.getByRole('button', { name: /merge/i });
    fireEvent.click(mergeBtn);
    // Merge takes latest (name: Janet) + my changed field (summary: mine).
    expect(onMerge).toHaveBeenCalledWith({ summary: 'mine', name: 'Janet' });
  });

  it('invokes callbacks for keep-mine and take-latest', () => {
    const onKeepMine = vi.fn();
    const onTakeLatest = vi.fn();
    render(
      <ConflictDialog
        mine={mine}
        latest={latest}
        currentVersion={5}
        onKeepMine={onKeepMine}
        onTakeLatest={onTakeLatest}
        onMerge={() => {}}
        onDismiss={() => {}}
      />
    );
    fireEvent.click(screen.getByRole('button', { name: /keep my changes/i }));
    fireEvent.click(screen.getByRole('button', { name: /take the latest/i }));
    expect(onKeepMine).toHaveBeenCalled();
    expect(onTakeLatest).toHaveBeenCalled();
  });

  it('dismisses on Escape (keyboard accessible)', () => {
    const onDismiss = vi.fn();
    render(
      <ConflictDialog
        mine={mine}
        latest={latest}
        currentVersion={5}
        onKeepMine={() => {}}
        onTakeLatest={() => {}}
        onMerge={() => {}}
        onDismiss={onDismiss}
      />
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onDismiss).toHaveBeenCalled();
  });
});

describe('SaveStatusChip', () => {
  it('shows an SR-labelled status for each state', () => {
    const { rerender } = render(<SaveStatusChip status="saving" lastSavedAt={null} />);
    expect(screen.getByRole('status')).toHaveTextContent(/saving/i);
    rerender(<SaveStatusChip status="offline" lastSavedAt={null} />);
    expect(screen.getByRole('status')).toHaveTextContent(/offline/i);
    rerender(<SaveStatusChip status="conflict" lastSavedAt={null} />);
    expect(screen.getByRole('status')).toHaveTextContent(/conflict/i);
    rerender(<SaveStatusChip status="retrying" lastSavedAt={null} />);
    expect(screen.getByRole('status')).toHaveTextContent(/retry/i);
  });

  it('shows a relative last-saved time when saved', () => {
    render(<SaveStatusChip status="saved" lastSavedAt={Date.now()} />);
    expect(screen.getByRole('status')).toHaveTextContent(/saved/i);
  });
});

describe('DegradationBanner', () => {
  it('renders nothing at full capability', () => {
    const { container } = render(<DegradationBanner level="full" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('names the offline level with an SR-friendly status', () => {
    render(<DegradationBanner level="offline-read-write" />);
    expect(screen.getByRole('status')).toHaveTextContent(/offline/i);
  });

  it('offers reload in safe-mode', () => {
    const onReload = vi.fn();
    render(<DegradationBanner level="safe-mode" onReload={onReload} />);
    fireEvent.click(screen.getByRole('button', { name: /reload/i }));
    expect(onReload).toHaveBeenCalled();
  });
});
