import type { SystemStatus } from '@/lib/api/config';

export type AiAvailabilityState = 'loading' | 'configured' | 'unconfigured' | 'status-error';
export type AiHealthState = 'unknown' | 'healthy' | 'unhealthy';

export interface AiAvailability {
  state: AiAvailabilityState;
  health: AiHealthState;
  canUseAi: boolean;
}

/**
 * Derive one fail-closed AI gate for every cost-bearing frontend action.
 * Provider health `null` remains unknown (the public status route deliberately
 * performs no live probe); only an explicit `false` is unhealthy.
 */
export function deriveAiAvailability(query: {
  data?: Pick<SystemStatus, 'llm_configured' | 'llm_healthy'>;
  isError?: boolean;
}): AiAvailability {
  if (query.data) {
    if (!query.data.llm_configured) {
      return { state: 'unconfigured', health: 'unknown', canUseAi: false };
    }
    const health: AiHealthState =
      query.data.llm_healthy === false
        ? 'unhealthy'
        : query.data.llm_healthy === true
          ? 'healthy'
          : 'unknown';
    // A known provider failure must block cost-bearing work until the user
    // repairs or re-tests the configuration. `null` remains usable because the
    // public status endpoint intentionally performs no live provider request.
    return { state: 'configured', health, canUseAi: health !== 'unhealthy' };
  }
  if (query.isError) {
    return { state: 'status-error', health: 'unknown', canUseAi: false };
  }
  return { state: 'loading', health: 'unknown', canUseAi: false };
}
