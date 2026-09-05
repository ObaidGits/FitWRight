/**
 * MCP access-token API (browser-authenticated).
 *
 * Mirrors the backend router (app/routers/mcp_tokens.py): the raw token value
 * appears in exactly ONE place - the POST response - and is display-only in the
 * UI (never persisted to localStorage or logs). The list endpoint returns
 * masked records with no token material. When MCP_ENABLED is off the whole
 * router 404s, which is how the Settings section detects "hidden".
 */
import { apiFetch } from './client';
import { parseError, readJson } from './errors';

/** A token as the backend's list/create endpoints report it (never the secret). */
export interface McpTokenRecord {
  id: string;
  label: string;
  created_at: string | null;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
}

export interface McpTokensResponse {
  items: McpTokenRecord[];
}

/** POST response: the masked record plus the raw token, shown once. */
export interface McpTokenCreated extends McpTokenRecord {
  token: string;
}

export async function fetchMcpTokens(): Promise<McpTokensResponse> {
  const res = await apiFetch('/mcp/tokens', { credentials: 'include' });
  return readJson<McpTokensResponse>(res, 'Failed to load MCP tokens.');
}

export async function createMcpToken(label: string): Promise<McpTokenCreated> {
  const res = await apiFetch('/mcp/tokens', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ label }),
  });
  return readJson<McpTokenCreated>(res, 'Failed to create MCP token.');
}

export async function revokeMcpToken(tokenId: string): Promise<void> {
  const res = await apiFetch(`/mcp/tokens/${tokenId}`, {
    method: 'DELETE',
    credentials: 'include',
  });
  if (!res.ok) throw await parseError(res, 'Failed to revoke MCP token.');
}
