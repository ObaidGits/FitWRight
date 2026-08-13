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

  /* The fill panel sits above the match badge when both are on screen. */
  .fillpanel { bottom: 20px; width: 290px; }
  .fillpanel .unanswered { display: flex; flex-direction: column; gap: 4px; }
  .fillpanel .jump {
    text-align: left; padding: 5px 7px; font-size: 12px; cursor: pointer;
    background: #f6f5f1; color: #1a1a17;
    border: 1px solid #e7e5df; border-radius: 6px;
  }
  .fillpanel .jump:hover { background: #efeee9; }
  .fillpanel .save {
    margin-top: 2px; padding: 7px 9px; font-size: 12px; font-weight: 600; cursor: pointer;
    background: #4f46e5; color: #fff; border: 0; border-radius: 6px;
  }
  .fillpanel .save:disabled { opacity: .6; cursor: default; }
  /* The promise, restated where the user is actually about to act. */
  .fillpanel .promise {
    margin-top: 6px; font-size: 11px; line-height: 1.4; opacity: .75; text-align: center;
  }

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
  // A floating score box over someone else's page needs to identify itself, and
  // the score is the content - so it is announced politely rather than silently
  // drawn. `complementary`, not `dialog`: nothing here demands a response.
  badge.setAttribute('role', 'complementary');
  badge.setAttribute('aria-label', 'FitWright resume match');

  const score = Math.round(match.match_score);
  const matched = match.matched.slice(0, 4);
  const missing = match.missing.slice(0, 3);
  const extra = match.matched.length - matched.length;

  badge.innerHTML = `
    <div class="row">
      <span class="brand">FitWright</span>
      <button class="close" aria-label="Hide the FitWright match badge" title="Hide">&times;</button>
    </div>
    ${
      match.degraded
        ? '<div class="muted">Match unavailable - add a parsed resume in FitWright.</div>'
        : `<div class="score" role="status" aria-live="polite" aria-label="${score} percent resume match">${score}%<small>resume match</small></div>
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
/** What the fill panel needs to describe a pass over the form. */
export interface FillSummary {
  filled: number;
  /** Questions we had nothing to answer with - the ones worth the user's eyes. */
  unanswered: { label: string; element: HTMLElement }[];
}

export interface FillPanelActions {
  /** Save the answers the user has typed since the fill. */
  onSaveAnswers: () => Promise<void> | void;
  onDismiss?: () => void;
}

/**
 * Summarise a fill and offer to learn from what the user types next.
 *
 * This is the whole point of learn-in-place: the moment a person is looking at a
 * form is the moment they know the answer, so asking then - and offering to
 * remember it - beats making them retype it in Settings later. Clicking an
 * unanswered question scrolls to it and focuses it, because "2 fields need you"
 * is useless if finding them is the hard part.
 */
export function showFillPanel(summary: FillSummary, actions: FillPanelActions): void {
  const root = ensureShadow();
  root.querySelector('.fillpanel')?.remove();

  const panel = document.createElement('div');
  panel.className = 'badge fillpanel';
  // Announced, not just drawn. This panel appears over someone else's page
  // without being asked for, so assistive technology has to be told what it is
  // and be able to leave it. `dialog` rather than `alertdialog`: it is
  // informative, and interrupting is not warranted.
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'false');
  panel.setAttribute('aria-label', 'FitWright autofill summary');

  const outstanding = summary.unanswered.length;
  panel.innerHTML = `
    <div class="row">
      <span class="brand">FitWright</span>
      <button class="close" aria-label="Hide the FitWright panel" title="Hide">&times;</button>
    </div>
    <div role="status" aria-live="polite"><strong>${summary.filled}</strong> field${summary.filled === 1 ? '' : 's'} filled${
      outstanding ? ` &middot; <strong>${outstanding}</strong> need${outstanding === 1 ? 's' : ''} you` : ''
    }</div>
    ${
      outstanding
        ? `<div class="muted" id="fw-panel-help">Click a question to jump to it. Answer them here, then save so the
             next form fills itself.</div>
           <div class="unanswered" role="group" aria-labelledby="fw-panel-help">${summary.unanswered
             .slice(0, 6)
             .map(
               (item, index) =>
                 `<button class="jump" data-index="${index}">${escapeHtml(item.label)}</button>`,
             )
             .join('')}</div>`
        : '<div class="muted">Nothing left unanswered on this step.</div>'
    }
    <button class="save">Save my answers to FitWright</button>
    <div class="promise">Nothing is submitted. Review, then press the employer's submit button.</div>
  `;

  function dismiss(): void {
    document.removeEventListener('keydown', onKeydown, true);
    panel.remove();
    actions.onDismiss?.();
  }

  // Escape closes it. Anything that covers part of a page the user is trying to
  // fill in must be dismissible without hunting for a small × - and capture phase
  // so a form that swallows keys on its own inputs cannot trap the user in here.
  function onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') dismiss();
  }
  document.addEventListener('keydown', onKeydown, true);

  panel.querySelector('.close')?.addEventListener('click', dismiss);

  for (const button of panel.querySelectorAll<HTMLButtonElement>('.jump')) {
    button.addEventListener('click', () => {
      const target = summary.unanswered[Number(button.dataset.index)]?.element;
      if (!target) return;
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      (target as HTMLInputElement).focus?.();
    });
  }

  const save = panel.querySelector<HTMLButtonElement>('.save');
  save?.addEventListener('click', async () => {
    save.disabled = true;
    save.textContent = 'Saving...';
    try {
      await actions.onSaveAnswers();
    } finally {
      save.disabled = false;
      save.textContent = 'Save my answers to FitWright';
    }
  });

  root.appendChild(panel);
}

export function hideFillPanel(): void {
  shadow?.querySelector('.fillpanel')?.remove();
}

export function toast(message: string, kind: 'ok' | 'err' | 'info' = 'info'): void {
  const root = ensureShadow();
  root.querySelectorAll('.toast').forEach((n) => n.remove());

  const el = document.createElement('div');
  el.className = `toast ${kind === 'info' ? '' : kind}`.trim();
  // Every toast this extension shows is a result the user asked for ("12 fields
  // filled", "sign in to this site first"), so it has to reach a screen reader.
  // `alert` for errors because they change what the user must do next; `status`
  // for the rest, which would otherwise interrupt their reading for good news.
  el.setAttribute('role', kind === 'err' ? 'alert' : 'status');
  el.setAttribute('aria-live', kind === 'err' ? 'assertive' : 'polite');
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
