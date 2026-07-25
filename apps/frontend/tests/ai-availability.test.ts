import { describe, expect, it } from 'vitest';

import { deriveAiAvailability } from '@/lib/ai-availability';

describe('deriveAiAvailability', () => {
  it('fails closed while status is loading or failed', () => {
    expect(deriveAiAvailability({})).toEqual({
      state: 'loading',
      health: 'unknown',
      canUseAi: false,
    });
    expect(deriveAiAvailability({ isError: true })).toEqual({
      state: 'status-error',
      health: 'unknown',
      canUseAi: false,
    });
  });

  it('distinguishes configuration from provider health', () => {
    expect(deriveAiAvailability({ data: { llm_configured: false, llm_healthy: null } })).toEqual({
      state: 'unconfigured',
      health: 'unknown',
      canUseAi: false,
    });
    expect(deriveAiAvailability({ data: { llm_configured: true, llm_healthy: null } })).toEqual({
      state: 'configured',
      health: 'unknown',
      canUseAi: true,
    });
    expect(deriveAiAvailability({ data: { llm_configured: true, llm_healthy: false } })).toEqual({
      state: 'configured',
      health: 'unhealthy',
      canUseAi: false,
    });
    expect(deriveAiAvailability({ data: { llm_configured: true, llm_healthy: true } })).toEqual({
      state: 'configured',
      health: 'healthy',
      canUseAi: true,
    });
  });
});
