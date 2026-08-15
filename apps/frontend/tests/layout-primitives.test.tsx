import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { Tabs, TabsList, TabsTrigger } from '@/components/atelier/tabs';
import { paneVisibility } from '@/components/layout/pane-toggle';
import { PAGE_WIDTH } from '@/lib/layout/page-width';

/**
 * Layout primitives that three or more pages depend on. Each of these was a real
 * defect found while auditing: the copy-per-page habit meant one page worked
 * around a bug locally while the others silently kept it.
 */
describe('TabsList overflow', () => {
  it('wraps and is bounded by its container, so a strip of tabs cannot overflow a phone', () => {
    // Application detail puts five tabs here ("Overview / Schedule / Cover
    // Letter / Interview Prep / Outreach"). As a non-wrapping inline-flex sized
    // to its content, that overflowed a 390px screen - clipping the last tabs or
    // pushing the whole page sideways.
    const { container } = render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">Overview</TabsTrigger>
          <TabsTrigger value="b">Interview Prep</TabsTrigger>
        </TabsList>
      </Tabs>
    );
    const list = container.querySelector('[role="tablist"]') as HTMLElement;
    expect(list.className).toContain('flex-wrap');
    expect(list.className).toContain('max-w-full');
  });

  it('still accepts per-page overrides', () => {
    const { container } = render(
      <Tabs defaultValue="a">
        <TabsList className="w-full justify-start">
          <TabsTrigger value="a">One</TabsTrigger>
        </TabsList>
      </Tabs>
    );
    const list = container.querySelector('[role="tablist"]') as HTMLElement;
    expect(list.className).toContain('justify-start');
    expect(list.className).toContain('flex-wrap');
  });
});

describe('paneVisibility', () => {
  // Below `lg` a two-pane surface shows one pane at a time; from `lg` up both
  // are always visible, so the breakpoint half must never be conditional.
  it('shows the selected pane and hides the other, below lg', () => {
    expect(paneVisibility(true)).toBe('block lg:block');
    expect(paneVisibility(false)).toBe('hidden lg:block');
  });

  it('supports a flex pane without dropping the lg override', () => {
    expect(paneVisibility(true, 'flex')).toBe('flex lg:flex');
    expect(paneVisibility(false, 'flex')).toBe('hidden lg:flex');
  });

  it('always restores visibility at lg, whichever pane is selected', () => {
    // The regression that matters: if the `lg:` half were ever made conditional,
    // one pane would vanish on desktop.
    for (const active of [true, false]) {
      expect(paneVisibility(active)).toContain('lg:block');
      expect(paneVisibility(active, 'flex')).toContain('lg:flex');
    }
  });
});

describe('PAGE_WIDTH', () => {
  it('gives WIDE no class, because the shell already caps content width', () => {
    // A WIDE page must add nothing; re-declaring the shell default is what left
    // three pages carrying a redundant max-w-6xl.
    expect(PAGE_WIDTH.WIDE).toBe('');
  });

  it('constrains the narrower tiers', () => {
    expect(PAGE_WIDTH.NARROW).toContain('max-w-2xl');
    expect(PAGE_WIDTH.CONTENT).toContain('max-w-4xl');
    for (const tier of [PAGE_WIDTH.NARROW, PAGE_WIDTH.CONTENT]) {
      expect(tier).toContain('mx-auto');
    }
  });
});
