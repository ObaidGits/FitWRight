import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { ContactCta } from '@/components/marketing/contact-cta';

/**
 * Home contact CTA: the primary action routes to /contact, the section has an
 * accessible heading, and trust signals + secondary channels are present.
 */
describe('ContactCta', () => {
  it('routes the primary CTA to the contact page', () => {
    render(<ContactCta />);
    expect(screen.getByRole('link', { name: /contact me/i })).toHaveAttribute('href', '/contact');
  });

  it('has an accessible heading and section label', () => {
    render(<ContactCta />);
    expect(screen.getByRole('heading', { name: /i’d love to hear it/i })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: /i’d love to hear it/i })).toBeInTheDocument();
  });

  it('surfaces trust signals and secondary channels', () => {
    render(<ContactCta />);
    expect(screen.getByText(/usually replies within a day/i)).toBeInTheDocument();
    expect(screen.getByText(/available for new work/i)).toBeInTheDocument();
    // Secondary channels (GitHub CTA + social links).
    expect(screen.getAllByRole('link', { name: /github/i }).length).toBeGreaterThan(0);
  });
});
