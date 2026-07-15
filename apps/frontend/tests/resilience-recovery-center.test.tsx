import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RecoveryCenter } from '@/components/resilience/recovery-center';
import type { OutboxEntry, QuarantineRecord } from '@/lib/resilience/local-store';

const quarantine: QuarantineRecord[] = [
  {
    id: 'draft:r1:123',
    reason: 'hash_mismatch',
    quarantinedAt: Date.now(),
    kind: 'draft',
    resumeId: 'r1',
    raw: { schemaVersion: 1 },
  },
];

const outbox: OutboxEntry[] = [
  {
    id: '000000000001',
    userId: 'u1',
    resumeId: 'r1',
    baseVersion: 1,
    idempotencyKey: 'k1',
    createdAt: Date.now(),
    attempts: 2,
    lastError: 'transient',
    bytes: 100,
    envelope: {
      schemaVersion: 1,
      contentHash: 'h',
      savedAt: 0,
      baseVersion: 1,
      encrypted: false,
      plain: {},
    },
  },
];

function setup(over: Partial<React.ComponentProps<typeof RecoveryCenter>> = {}) {
  const props = {
    quarantine,
    outbox,
    onExportQuarantine: vi.fn(),
    onDiscardQuarantine: vi.fn(),
    onDiscardOutbox: vi.fn(),
    onRetrySync: vi.fn(),
    onClose: vi.fn(),
    ...over,
  };
  render(<RecoveryCenter {...props} />);
  return props;
}

describe('RecoveryCenter', () => {
  it('lists quarantined records and queued outbox entries', () => {
    setup();
    expect(screen.getByRole('dialog', { name: /recovery center/i })).toBeInTheDocument();
    expect(screen.getByText(/Quarantined items \(1\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Queued offline edits \(1\)/i)).toBeInTheDocument();
    expect(screen.getByText(/hash_mismatch/i)).toBeInTheDocument();
    expect(screen.getByText(/2 attempt\(s\)/i)).toBeInTheDocument();
  });

  it('exports and discards a quarantined record', () => {
    const props = setup();
    fireEvent.click(screen.getByRole('button', { name: /export quarantined record/i }));
    expect(props.onExportQuarantine).toHaveBeenCalledWith('draft:r1:123');
    fireEvent.click(screen.getByRole('button', { name: /discard quarantined record/i }));
    expect(props.onDiscardQuarantine).toHaveBeenCalledWith('draft:r1:123');
  });

  it('retries sync and discards an outbox entry', () => {
    const props = setup();
    fireEvent.click(screen.getByRole('button', { name: /retry sync/i }));
    expect(props.onRetrySync).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /discard queued edit/i }));
    expect(props.onDiscardOutbox).toHaveBeenCalledWith('000000000001');
  });

  it('closes on Escape (keyboard accessible)', () => {
    const props = setup();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(props.onClose).toHaveBeenCalled();
  });

  it('shows healthy empty states when nothing to recover', () => {
    setup({ quarantine: [], outbox: [] });
    expect(screen.getByText(/local data is healthy/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing queued/i)).toBeInTheDocument();
  });
});
