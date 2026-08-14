/**
 * The upload gate must name the real problem.
 *
 * Two reported bugs: a missing provider key produced a network/offline-sounding
 * complaint, and the upload was allowed to start and run to completion before
 * failing. `deriveAiAvailability` is the gate the import page consults, so these
 * assert the states it must distinguish - a key that is absent, and a key that
 * exists but was refused. Both block, for different reasons and with different
 * instructions to the user.
 */

import { describe, expect, it } from 'vitest';

import { deriveAiAvailability } from '@/lib/ai-availability';

describe('AI gate before uploading a resume', () => {
  it('blocks when no provider key is configured', () => {
    const gate = deriveAiAvailability({ data: { llm_configured: false, llm_healthy: null } });
    expect(gate.canUseAi).toBe(false);
    expect(gate.state).toBe('unconfigured');
  });

  it('blocks when a key exists but the provider refused it', () => {
    // The case that used to slip through: configured is true because the
    // deployment-level LLM_API_KEY satisfies it, so only health can say no.
    const gate = deriveAiAvailability({ data: { llm_configured: true, llm_healthy: false } });
    expect(gate.canUseAi).toBe(false);
    expect(gate.state).toBe('configured');
    expect(gate.health).toBe('unhealthy');
  });

  it('allows upload when a key is configured and nothing has refused it', () => {
    const gate = deriveAiAvailability({ data: { llm_configured: true, llm_healthy: null } });
    expect(gate.canUseAi).toBe(true);
    expect(gate.health).toBe('unknown');
  });

  it('blocks while status is still loading rather than guessing', () => {
    expect(deriveAiAvailability({}).canUseAi).toBe(false);
    expect(deriveAiAvailability({ isError: true }).canUseAi).toBe(false);
  });
});
