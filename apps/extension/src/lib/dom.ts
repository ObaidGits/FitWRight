/**
 * DOM helpers for content scripts.
 *
 * The non-obvious part is `setValue`. Modern application forms (Greenhouse,
 * Ashby, Workday) are React/Vue controlled inputs: assigning `el.value` updates
 * the DOM but not the framework's internal state, so the value visibly appears
 * and is then wiped on the next render or ignored on submit. Writing through the
 * prototype's native setter and then dispatching bubbling `input`/`change`
 * events is what makes the framework actually observe the change.
 */

export type Fillable = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;

/** First matching element for any of the given selectors. */
export function pick<T extends Element = Element>(
  selectors: string[],
  root: ParentNode = document,
): T | null {
  for (const selector of selectors) {
    try {
      const found = root.querySelector<T>(selector);
      if (found) return found;
    } catch {
      /* invalid selector - skip rather than abort the whole lookup */
    }
  }
  return null;
}

/** Trimmed text of the first matching element. */
export function pickText(selectors: string[], root: ParentNode = document): string {
  const el = pick<HTMLElement>(selectors, root);
  return el ? clean(el.textContent ?? '') : '';
}

/** Collapse whitespace and trim - scraped text is full of newlines and nbsp. */
export function clean(value: string): string {
  return value.replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
}

/** Readable text of an element subtree, preserving line structure. */
export function blockText(el: Element | null): string {
  if (!el) return '';
  return (el as HTMLElement).innerText
    .replace(/\u00a0/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/** True when the element is actually on screen and interactive. */
export function isVisible(el: Element): boolean {
  const html = el as HTMLElement;
  if (html.hidden) return false;
  const style = window.getComputedStyle(html);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
    return false;
  }
  const rect = html.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

/** True when the field can be written to. */
export function isFillable(el: Fillable): boolean {
  if (el.disabled || (el as HTMLInputElement).readOnly) return false;
  const type = (el as HTMLInputElement).type?.toLowerCase();
  if (type && ['hidden', 'submit', 'button', 'reset', 'image', 'password'].includes(type)) {
    return false;
  }
  return isVisible(el);
}

/**
 * The human-readable label for a field, from whichever source the page provides.
 * Order matters: an explicit `<label for>` beats a guessed ancestor, and both
 * beat attribute names like `cand_first_nm`.
 */
export function labelFor(el: Fillable): string {
  const id = el.getAttribute('id');
  if (id) {
    const escaped = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(id) : id;
    const explicit = document.querySelector<HTMLLabelElement>(`label[for="${escaped}"]`);
    if (explicit) return clean(explicit.textContent ?? '');
  }

  const wrapping = el.closest('label');
  if (wrapping) {
    // Strip the field's own text (selects contribute their options) so only the
    // question text remains.
    const clone = wrapping.cloneNode(true) as HTMLElement;
    clone.querySelectorAll('input, textarea, select').forEach((n) => n.remove());
    const text = clean(clone.textContent ?? '');
    if (text) return text;
  }

  const labelledBy = el.getAttribute('aria-labelledby');
  if (labelledBy) {
    const parts = labelledBy
      .split(/\s+/)
      .map((refId) => document.getElementById(refId)?.textContent ?? '')
      .filter(Boolean);
    if (parts.length) return clean(parts.join(' '));
  }

  const aria = el.getAttribute('aria-label');
  if (aria) return clean(aria);

  // Last resort: a nearby label-ish node in the same field group.
  const group = el.closest('[class*="field"], [class*="form-group"], [class*="question"], fieldset');
  if (group) {
    const candidate = group.querySelector('label, legend, .label, [class*="Label"]');
    if (candidate) return clean(candidate.textContent ?? '');
  }
  return '';
}

/** All the strings worth pattern-matching a field against. */
export function fieldSignals(el: Fillable): string {
  return [
    labelFor(el),
    el.getAttribute('name') ?? '',
    el.getAttribute('id') ?? '',
    el.getAttribute('placeholder') ?? '',
    el.getAttribute('aria-label') ?? '',
    el.getAttribute('autocomplete') ?? '',
    el.getAttribute('data-testid') ?? '',
    el.getAttribute('data-automation-id') ?? '', // Workday
  ]
    .join(' ')
    .toLowerCase();
}

/**
 * Set a field's value so React/Vue-controlled inputs actually register it.
 * Returns false when the value could not be applied (e.g. no matching option)
 * OR when a read-back immediately after dispatch shows the framework reverted
 * it - a controlled input that rejects an update on its next render looks
 * identical to success until you check.
 */
export function setValue(el: Fillable, value: string): boolean {
  if (el instanceof HTMLSelectElement) return setSelectValue(el, value);

  const proto =
    el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;

  el.focus();
  if (setter) setter.call(el, value);
  else el.value = value;

  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  el.blur();
  return readsBack(el, value);
}

/**
 * Re-read the element's actual value and compare it to what was intended.
 *
 * Exported so callers that need the finer-grained signal - "filled" vs "filled
 * AND verified" - can record them separately (see the brain decision trail in
 * content/autofill.ts) rather than only getting setValue's collapsed boolean.
 */
export function readsBack(el: Fillable, intended: string): boolean {
  return el.value.trim() === intended.trim();
}

/** Choose the option that best matches `value`, exact match preferred. */
function setSelectValue(el: HTMLSelectElement, value: string): boolean {
  const wanted = value.trim().toLowerCase();
  if (!wanted) return false;

  const options = Array.from(el.options);
  const exact = options.find(
    (o) => o.value.toLowerCase() === wanted || clean(o.text).toLowerCase() === wanted,
  );
  const partial =
    exact ??
    options.find((o) => {
      const text = clean(o.text).toLowerCase();
      return text.includes(wanted) || wanted.includes(text);
    });

  if (!partial) return false;
  el.focus();
  el.value = partial.value;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  el.blur();
  return el.value === partial.value;
}

/** Check a radio/checkbox whose label matches `value`. */
export function setRadioGroup(name: string, value: string, root: ParentNode = document): boolean {
  const wanted = value.trim().toLowerCase();
  if (!wanted) return false;

  const escaped = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(name) : name;
  const inputs = Array.from(
    root.querySelectorAll<HTMLInputElement>(
      `input[type="radio"][name="${escaped}"], input[type="checkbox"][name="${escaped}"]`,
    ),
  );

  for (const input of inputs) {
    const text = `${labelFor(input)} ${input.value}`.toLowerCase();
    if (text.includes(wanted)) {
      input.click(); // click() drives framework handlers; .checked = true does not
      return true;
    }
  }
  return false;
}

/** Attach a File to a file input via DataTransfer (the only way that works). */
export function setFileInput(el: HTMLInputElement, file: File): boolean {
  try {
    const transfer = new DataTransfer();
    transfer.items.add(file);
    el.files = transfer.files;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  } catch {
    return false;
  }
}

/** Rebuild a File from the data URL the service worker sent us. */
export function fileFromDataUrl(dataUrl: string, filename: string): File | null {
  try {
    const [header, base64] = dataUrl.split(',');
    if (!base64) return null;
    const mime = /:(.*?);/.exec(header)?.[1] ?? 'application/pdf';
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return new File([bytes], filename, { type: mime });
  } catch {
    return null;
  }
}

/** Read a JSON-LD block of the given @type, if the page publishes one. */
export function readJsonLd(type: string): Record<string, unknown> | null {
  const blocks = document.querySelectorAll<HTMLScriptElement>(
    'script[type="application/ld+json"]',
  );
  for (const block of blocks) {
    try {
      const parsed = JSON.parse(block.textContent ?? '');
      const candidates: unknown[] = Array.isArray(parsed)
        ? parsed
        : Array.isArray((parsed as { '@graph'?: unknown[] })['@graph'])
          ? ((parsed as { '@graph': unknown[] })['@graph'] as unknown[])
          : [parsed];
      for (const candidate of candidates) {
        const node = candidate as Record<string, unknown>;
        const nodeType = node?.['@type'];
        const types = Array.isArray(nodeType) ? nodeType : [nodeType];
        if (types.includes(type)) return node;
      }
    } catch {
      /* malformed JSON-LD is common - skip this block */
    }
  }
  return null;
}

/** Wait for a selector to appear, for SPA pages that render after load. */
export function waitFor(selector: string, timeoutMs = 8000): Promise<Element | null> {
  const existing = document.querySelector(selector);
  if (existing) return Promise.resolve(existing);

  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      observer.disconnect();
      resolve(null);
    }, timeoutMs);

    const observer = new MutationObserver(() => {
      const found = document.querySelector(selector);
      if (found) {
        clearTimeout(timer);
        observer.disconnect();
        resolve(found);
      }
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  });
}
