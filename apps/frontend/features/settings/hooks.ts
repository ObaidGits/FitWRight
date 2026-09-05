'use client';

/** Settings data hooks (Task 13) - wrap the existing config API via Query. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/query/client';
import { ApiError } from '@/lib/api/errors';
import { createMcpToken, fetchMcpTokens, revokeMcpToken, type McpTokenRecord } from '@/lib/api/mcp';
import {
  fetchLlmConfig,
  updateLlmConfig,
  testLlmConnection,
  fetchFeatureConfig,
  updateFeatureConfig,
  fetchLanguageConfig,
  updateLanguageConfig,
  fetchApiKeyStatus,
  updateApiKeys,
  deleteApiKey,
  fetchFeaturePrompts,
  updateFeaturePrompts,
  type LLMConfigUpdate,
  type FeatureConfigUpdate,
  type LanguageConfigUpdate,
  type ApiKeysUpdateRequest,
  type ApiKeyProvider,
  type FeaturePromptsUpdate,
} from '@/lib/api/config';

export function useLlmConfig() {
  return useQuery({ queryKey: ['config', 'llm'], queryFn: fetchLlmConfig });
}
export function useApiKeyStatus() {
  return useQuery({ queryKey: ['config', 'api-keys'], queryFn: fetchApiKeyStatus });
}
export function useFeatureConfig() {
  return useQuery({ queryKey: ['config', 'features'], queryFn: fetchFeatureConfig });
}
export function useLanguageConfig() {
  return useQuery({ queryKey: ['config', 'language'], queryFn: fetchLanguageConfig });
}

export function useUpdateLlmConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (u: LLMConfigUpdate) => updateLlmConfig(u),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['config'] });
      qc.invalidateQueries({ queryKey: queryKeys.status });
      qc.invalidateQueries({ queryKey: queryKeys.setup });
    },
  });
}
export function useUpdateFeatureConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (u: FeatureConfigUpdate) => updateFeatureConfig(u),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['config', 'features'] }),
  });
}
export function useUpdateLanguageConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (u: LanguageConfigUpdate) => updateLanguageConfig(u),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['config', 'language'] }),
  });
}
export function useTestConnection() {
  return useMutation({ mutationFn: (u?: LLMConfigUpdate) => testLlmConnection(u) });
}
export function useFeaturePrompts() {
  return useQuery({ queryKey: ['config', 'feature-prompts'], queryFn: fetchFeaturePrompts });
}
export function useUpdateFeaturePrompts() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (u: FeaturePromptsUpdate) => updateFeaturePrompts(u),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['config', 'feature-prompts'] }),
  });
}
export function useUpdateApiKeys() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (keys: ApiKeysUpdateRequest) => updateApiKeys(keys),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['config'] });
      qc.invalidateQueries({ queryKey: queryKeys.status });
      qc.invalidateQueries({ queryKey: queryKeys.setup });
    },
  });
}
export function useDeleteApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (provider: ApiKeyProvider) => deleteApiKey(provider),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['config'] });
      qc.invalidateQueries({ queryKey: queryKeys.status });
      qc.invalidateQueries({ queryKey: queryKeys.setup });
    },
  });
}

// ---------------------------------------------------------------------------
// MCP access tokens (Settings > Account > MCP / API access)
// ---------------------------------------------------------------------------

/**
 * The MCP section's view of the token list, including whether MCP exists at
 * all. There is no separate feature-flag endpoint: when the deployment has
 * MCP_ENABLED off the backend 404s the whole /mcp/tokens router, and that 404
 * is turned into `enabled: false` here rather than an error, so react-query
 * does not treat a perfectly normal "not installed" state as a failure.
 */
export interface McpTokensView {
  enabled: boolean;
  items: McpTokenRecord[];
}

export function useMcpTokens() {
  return useQuery({
    queryKey: ['mcp', 'tokens'],
    queryFn: async (): Promise<McpTokensView> => {
      try {
        const res = await fetchMcpTokens();
        return { enabled: true, items: res.items };
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          return { enabled: false, items: [] };
        }
        throw err;
      }
    },
  });
}

/**
 * Create an MCP token. The raw token travels ONLY in the mutation result -
 * it is the caller's (one-time reveal) responsibility, never cache state.
 *
 * `gcTime: 0` (vs the 5-minute default): the mutation's cached `data` would
 * otherwise keep the raw token in react-query's MutationCache long after the
 * reveal dialog is dismissed and the Settings page unmounts. With 0, the
 * cache entry is dropped as soon as its last observer unsubscribes, so the
 * only copy of the token is the component's in-memory reveal state.
 */
export function useCreateMcpToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (label: string) => createMcpToken(label),
    gcTime: 0,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mcp', 'tokens'] });
    },
  });
}

export function useRevokeMcpToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tokenId: string) => revokeMcpToken(tokenId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mcp', 'tokens'] });
    },
  });
}
