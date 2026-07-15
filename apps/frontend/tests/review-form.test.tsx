import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * Review form: accessible star rating (radiogroup), validation, honeypot/timing
 * payload, success state, and error preservation.
 */

const submitReviewMock = vi.fn();
vi.mock('@/lib/api/reviews', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, submitReview: (...a: unknown[]) => submitReviewMock(...a) };
});

import { ReviewForm } from '@/components/connect/review-form';

function fillValid() {
  fireEvent.click(screen.getByRole('radio', { name: /5 stars/i }));
  fireEvent.change(screen.getByLabelText('Headline'), { target: { value: 'Fantastic tool' } });
  fireEvent.change(screen.getByLabelText('Your review'), {
    target: { value: 'It tailored my resume perfectly and saved me hours of manual editing.' },
  });
}

afterEach(() => vi.clearAllMocks());

describe('ReviewForm', () => {
  it('exposes an accessible 5-star radiogroup', () => {
    render(<ReviewForm />);
    expect(screen.getByRole('radiogroup', { name: /your rating/i })).toBeInTheDocument();
    expect(screen.getAllByRole('radio')).toHaveLength(5);
  });

  it('blocks submit without a rating and shows an error', async () => {
    render(<ReviewForm />);
    fireEvent.change(screen.getByLabelText('Headline'), { target: { value: 'Nice' } });
    fireEvent.change(screen.getByLabelText('Your review'), {
      target: { value: 'A perfectly long enough review body for validation.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /submit review/i }));
    await waitFor(() => expect(screen.getByText(/please pick a rating/i)).toBeInTheDocument());
    expect(submitReviewMock).not.toHaveBeenCalled();
  });

  it('submits a valid review with rating + honeypot/timing and shows success', async () => {
    submitReviewMock.mockResolvedValue({ message: 'ok', reference: 'rev123' });
    render(<ReviewForm />);
    fillValid();
    fireEvent.click(screen.getByRole('button', { name: /submit review/i }));

    await waitFor(() => expect(submitReviewMock).toHaveBeenCalled());
    const payload = submitReviewMock.mock.calls[0][0];
    expect(payload.rating).toBe(5);
    expect(payload.title).toBe('Fantastic tool');
    expect(payload.company_website).toBe('');
    expect(typeof payload.elapsed_ms).toBe('number');

    await waitFor(() => expect(screen.getByText(/thank you for the review/i)).toBeInTheDocument());
  });

  it('omits the name when posting anonymously', async () => {
    submitReviewMock.mockResolvedValue({ message: 'ok', reference: 'rev124' });
    render(<ReviewForm />);
    fillValid();
    fireEvent.change(screen.getByLabelText('Your name (optional)'), {
      target: { value: 'Grace' },
    });
    fireEvent.click(screen.getByRole('switch', { name: /post anonymously/i }));
    fireEvent.click(screen.getByRole('button', { name: /submit review/i }));
    await waitFor(() => expect(submitReviewMock).toHaveBeenCalled());
    expect(submitReviewMock.mock.calls[0][0].name).toBeUndefined();
  });
});
