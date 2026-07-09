'use client';

/**
 * Scroll-reveal wrapper (homepage). Progressive enhancement: content is fully
 * visible without JS (a <noscript> rule forces .reveal visible); with JS it
 * fades/slides in when it enters the viewport. Honours prefers-reduced-motion
 * via the global `.atelier` reduced-motion rule (transitions collapse to ~0).
 */
import * as React from 'react';
import { cn } from '@/lib/utils';

interface RevealProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Stagger delay in ms (for sequential children). */
  delay?: number;
  as?: 'div' | 'section' | 'li' | 'span';
}

export function Reveal({
  children,
  className,
  delay = 0,
  as = 'div',
  style,
  ...rest
}: RevealProps) {
  const ref = React.useRef<HTMLDivElement | null>(null);
  const [visible, setVisible] = React.useState(false);

  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === 'undefined') {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setVisible(true);
            observer.disconnect();
          }
        }
      },
      { threshold: 0.15, rootMargin: '0px 0px -8% 0px' }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const Tag = as as React.ElementType;
  return (
    <Tag
      ref={ref}
      className={cn('reveal', visible && 'is-visible', className)}
      style={{ transitionDelay: `${delay}ms`, ...style }}
      {...rest}
    >
      {children}
    </Tag>
  );
}
