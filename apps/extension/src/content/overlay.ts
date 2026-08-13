/**
 * In-page UI: the match badge and toasts.
 *
 * Everything renders inside a shadow root. That is not decoration - a content
 * script injects into pages whose CSS we do not control, and without shadow
 * isolation the host page's `* { box-sizing }`, aggressive resets or `z-index`
 * stacking routinely mangle injected UI. The shadow boundary makes our styles
 * unreachable from the page and vice versa.
 */
import { t } from '@/lib/i18n';
import { setSitePreference } from '@/lib/site-prefs';
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
  /* A quieter sibling of the close button: closing is for now, this is for good. */
  .badge .never {
    margin-left: auto; margin-right: 6px; background: none; border: 0; padding: 0;
    font: inherit; font-size: 10px; color: inherit; opacity: .65; cursor: pointer;
    text-decoration: underline;
  }
  .badge .never:hover { opacity: 1; }
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
  badge.setAttribute('aria-label', t('badgeLabel'));

  const score = Math.round(match.match_score);
  const matched = match.matched.slice(0, 4);
  const missing = match.missing.slice(0, 3);
  const extra = match.matched.length - matched.length;

  badge.innerHTML = `
    <div class="row">
      <span class="brand">FitWright</span>
      <button class="close" aria-label="${t('badgeHide')}" title="${t('badgeHide')}">&times;</button>
    </div>
    ${
      match.degraded
        ? `<div class="muted">${t('badgeMatchUnavailable')}</div>`
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
  panel.setAttribute('aria-label', t('panelLabel'));

  const outstanding = summary.unanswered.length;
  panel.innerHTML = `
    <div class="row">
      <span class="brand" data-drag="1" title="${t('panelDragTitle')}">FitWright</span>
      <button class="never" title="${t('panelNotHereTitle')}">${t('panelNotHere')}</button>
      <button class="close" aria-label="${t('panelHide')}" title="${t('panelHide')}">&times;</button>
    </div>
    <div role="status" aria-live="polite"><strong>${summary.filled}</strong> field${summary.filled === 1 ? '' : 's'} filled${
      outstanding ? ` &middot; <strong>${outstanding}</strong> need${outstanding === 1 ? 's' : ''} you` : ''
    }</div>
    ${
      outstanding
        ? `<div class="muted" id="fw-panel-help">${t('panelJumpHelp')}</div>
           <div class="unanswered" role="group" aria-labelledby="fw-panel-help">${summary.unanswered
             .slice(0, 6)
             .map(
               (item, index) =>
                 `<button class="jump" data-index="${index}">${escapeHtml(item.label)}</button>`,
             )
             .join('')}</div>`
        : `<div class="muted">${t('panelNothingOutstanding')}</div>`
    }
    <button class="save">${t('panelSaveAnswers')}</button>
    <div class="promise">${t('panelNeverSubmits')}</div>
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

  // "Not here" is a different intention from closing: autofill keeps working on
  // this site, the box just stops appearing. Without it, a form whose own buttons
  // sit bottom-right leaves the user closing this every single time.
  panel.querySelector('.never')?.addEventListener('click', () => {
    void setSitePreference(location.hostname, { panelHidden: true });
    dismiss();
  });

  makeDraggable(panel, panel.querySelector('[data-drag]'));

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

/**
 * Let the user move a floating panel out of the way.
 *
 * It sits bottom-right at a very high z-index, which on some application forms is
 * exactly where the site's own Next/Submit buttons are. Dragging is the difference
 * between "helpful overlay" and "thing I have to close to finish my application".
 *
 * Position is per page load, deliberately not persisted: the right place depends
 * on the form, and remembering a corner chosen on one site would put it somewhere
 * unhelpful on the next.
 */
function makeDraggable(panel: HTMLElement, handle: Element | null): void {
  if (!handle) return;
  (handle as HTMLElement).style.cursor = 'grab';

  let startX = 0;
  let startY = 0;
  let originLeft = 0;
  let originTop = 0;

  function onPointerDown(event: Event): void {
    const pointer = event as PointerEvent;
    const rect = panel.getBoundingClientRect();
    startX = pointer.clientX;
    startY = pointer.clientY;
    originLeft = rect.left;
    originTop = rect.top;

    // Switch from the right/bottom anchoring to explicit coordinates, or the
    // first drag would fight the CSS that positions it.
    panel.style.left = `${originLeft}px`;
    panel.style.top = `${originTop}px`;
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';

    document.addEventListener('pointermove', onPointerMove, true);
    document.addEventListener('pointerup', onPointerUp, true);
    event.preventDefault();
  }

  function onPointerMove(event: Event): void {
    const pointer = event as PointerEvent;
    // Clamped so the panel can never be dragged off screen and lost.
    const maxLeft = Math.max(0, window.innerWidth - panel.offsetWidth);
    const maxTop = Math.max(0, window.innerHeight - panel.offsetHeight);
    const left = Math.min(maxLeft, Math.max(0, originLeft + (pointer.clientX - startX)));
    const top = Math.min(maxTop, Math.max(0, originTop + (pointer.clientY - startY)));
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
  }

  function onPointerUp(): void {
    document.removeEventListener('pointermove', onPointerMove, true);
    document.removeEventListener('pointerup', onPointerUp, true);
  }

  handle.addEventListener('pointerdown', onPointerDown);
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
