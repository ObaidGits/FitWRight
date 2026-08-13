/**
 * In-page UI: the match badge and toasts.
 *
 * Everything renders inside a shadow root. That is not decoration - a content
 * script injects into pages whose CSS we do not control, and without shadow
 * isolation the host page's `* { box-sizing }`, aggressive resets or `z-index`
 * stacking routinely mangle injected UI. The shadow boundary makes our styles
 * unreachable from the page and vice versa.
 */
import type { MatchResult } from '@/lib/types';

const HOST_ID = 'fitwright-companion-root';

let shadow: ShadowRoot | null = null;

/** Create (once) and return the shadow root all our UI lives in. */
function ensureShadow(): ShadowRoot {
  if (shadow) return shadow;

  const existing = document.getElementById(HOST_ID);
  if (existing?.shadowRoot) {
    shadow = existing.shadowRoot;
    return shadow;
  }

  const host = document.createElement('div');
  host.id = HOST_ID;
  document.body.appendChild(host);

  shadow = host.attachShadow({ mode: 'open' });
  const style = document.createElement('style');
  style.textContent = STYLES;
  shadow.appendChild(style);
  return shadow;
}

/** Component styles - scoped to the shadow root, so plain class names are safe. */
const STYLES = `
  :host { all: initial; }
  * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }

  .badge {
    position: fixed; right: 20px; bottom: 20px; z-index: 2147483000;
    display: flex; flex-direction: column; gap: 8px;
    width: 260px; padding: 12px 14px;
    background: #ffffff; color: #1a1a17;
    border: 1px solid #e7e5df; border-radius: 10px;
    box-shadow: 0 6px 24px rgba(0,0,0,.14);
    font-size: 13px; line-height: 1.4;
    animation: rise .18s cubic-bezier(.16,1,.3,1);
  }
  @keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }

  .row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .brand { font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: #6b6b63; }
  .close { border: 0; background: none; cursor: pointer; color: #6b6b63; font-size: 15px; line-height: 1; padding: 2px 4px; border-radius: 4px; }
  .close:hover { background: #f0efea; color: #1a1a17; }

  .score { font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; color: #4f46e5; }
  .score small { font-size: 11px; font-weight: 500; color: #6b6b63; margin-left: 4px; }

  .pills { display: flex; flex-wrap: wrap; gap: 4px; }
  .pill { font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 999px; }
  .pill.hit { background: rgba(21,128,61,.12); color: #15803d; }
  .pill.miss { background: rgba(220,38,38,.10); color: #dc2626; }
  .more { font-size: 10px; color: #6b6b63; align-self: center; }

  .actions { display: flex; gap: 6px; }
  button.act {
    flex: 1; padding: 6px 8px; font-size: 12px; font-weight: 600; cursor: pointer;
    border-radius: 6px; border: 1px solid #e7e5df; background: #fff; color: #1a1a17;
  }
  button.act:hover { background: #f6f5f2; }
  button.act.primary { background: #4f46e5; border-color: #4f46e5; color: #fff; }
  button.act.primary:hover { filter: brightness(1.08); }
  button.act:disabled { opacity: .55; cursor: default; }

  .muted { color: #6b6b63; font-size: 12px; }

  .toast {
    position: fixed; right: 20px; bottom: 20px; z-index: 2147483001;
    max-width: 300px; padding: 10px 14px; border-radius: 8px;
    font-size: 13px; font-weight: 500; color: #fff; background: #1a1a17;
    box-shadow: 0 6px 24px rgba(0,0,0,.2);
    animation: rise .18s cubic-bezier(.16,1,.3,1);
  }
  .toast.ok { background: #15803d; }
  .toast.err { background: #dc2626; }

  @media (prefers-color-scheme: dark) {
    .badge { background: #201f1a; color: #f2f0ea; border-color: #302e26; }
    .close:hover { background: #2b2a22; color: #f2f0ea; }
    button.act { background: #26251f; border-color: #302e26; color: #f2f0ea; }
    button.act:hover { background: #2b2a22; }
    .brand, .muted, .more, .score small { color: #a5a294; }
  }
`;

export interface BadgeActions {
  onTailor: () => void;
  onSave: () => void;
  onDismiss: () => void;
}

/** Render (or re-render) the match badge. */
export function showBadge(match: MatchResult, actions: BadgeActions): void {
  const root = ensureShadow();
  root.querySelector('.badge')?.remove();

  const badge = document.createElement('div');
  badge.className = 'badge';

  const score = Math.round(match.match_score);
  const matched = match.matched.slice(0, 4);
  const missing = match.missing.slice(0, 3);
  const extra = match.matched.length - matched.length;

  badge.innerHTML = `
    <div class="row">
      <span class="brand">FitWright</span>
      <button class="close" title="Hide">&times;</button>
    </div>
    ${
      match.degraded
        ? '<div class="muted">Match unavailable - add a parsed resume in FitWright.</div>'
        : `<div class="score">${score}%<small>resume match</small></div>
           <div class="pills">
             ${matched.map((k) => `<span class="pill hit">${escapeHtml(k)}</span>`).join('')}
             ${missing.map((k) => `<span class="pill miss">${escapeHtml(k)}</span>`).join('')}
             ${extra > 0 ? `<span class="more">+${extra}</span>` : ''}
           </div>`
    }
    <div class="actions">
      <button class="act" data-action="save">Save</button>
      <button class="act primary" data-action="tailor">Tailor resume</button>
    </div>
  `;

  badge.querySelector('.close')?.addEventListener('click', () => {
    badge.remove();
    actions.onDismiss();
  });
  badge
    .querySelector('[data-action="save"]')
    ?.addEventListener('click', () => actions.onSave());
  badge
    .querySelector('[data-action="tailor"]')
    ?.addEventListener('click', () => actions.onTailor());

  root.appendChild(badge);
}

/** Replace the badge body with a one-line status (used while scoring). */
export function showBadgeLoading(text = 'Scoring this job...'): void {
  const root = ensureShadow();
  root.querySelector('.badge')?.remove();

  const badge = document.createElement('div');
  badge.className = 'badge';
  badge.innerHTML = `
    <div class="row"><span class="brand">FitWright</span></div>
    <div class="muted">${escapeHtml(text)}</div>
  `;
  root.appendChild(badge);
}

export function hideBadge(): void {
  shadow?.querySelector('.badge')?.remove();
}

/** Transient status message. */
export function toast(message: string, kind: 'ok' | 'err' | 'info' = 'info'): void {
  const root = ensureShadow();
  root.querySelectorAll('.toast').forEach((n) => n.remove());

  const el = document.createElement('div');
  el.className = `toast ${kind === 'info' ? '' : kind}`.trim();
  el.textContent = message;
  root.appendChild(el);

  setTimeout(() => el.remove(), 3200);
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
