/**
 * <JsonLd> - renders one or more schema.org objects as a single, safely
 * serialized `application/ld+json` script. Server-rendered so crawlers and AI
 * retrieval systems see structured data in the initial HTML.
 *
 * Passing an array emits a JSON-LD graph in one script tag, which is the
 * recommended way to express multiple related entities on a page.
 */
import * as React from 'react';

type JsonLdData = Record<string, unknown>;

/** Escape `<` to prevent breaking out of the script context (XSS hardening). */
function serialize(data: JsonLdData | JsonLdData[]): string {
  return JSON.stringify(data).replace(/</g, '\\u003c');
}

export function JsonLd({ data }: { data: JsonLdData | JsonLdData[] }) {
  return (
    <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: serialize(data) }} />
  );
}
