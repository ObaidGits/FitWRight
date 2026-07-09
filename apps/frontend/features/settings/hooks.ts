'use client';

/** Settings data hooks (Task 13) — wrap the existing config API via Query. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
  type LLMConfigUpdate,
  type FeatureConfigUpdate,
  type LanguageConfigUpdate,
  type ApiKeysUpdateRequest,
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
      qc.invalidateQueries({ queryKey: ['status'] });
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
export function useUpdateApiKeys() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (keys: ApiKeysUpdateRequest) => updateApiKeys(keys),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['config'] });
      qc.invalidateQueries({ queryKey: ['status'] });
    },
  });
}
