import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * Contact form: inline validation, progressive disclosure, honeypot/timing
 * spam fields, successful submission -> success card, and error preservation.
 */

const submitContactMock = vi.fn();
vi.mock('@/lib/api/contact', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, submitContact: (...a: unknown[]) => submitContactMock(...a) };
});

import { ContactForm } from '@/components/contact/contact-form';

function fill() {
  fireEvent.change(screen.getByLabelText('Your name'), { target: { value: 'Ada Lovelace' } });
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'ada@example.com' } });
  fireEvent.change(screen.getByLabelText('Subject'), { target: { value: 'Hello there' } });
  fireEvent.change(screen.getByLabelText('Message'), {
    target: { value: 'I would love to collaborate on your tailoring engine.' },
  });
}

afterEach(() => vi.clearAllMocks());

describe('ContactForm', () => {
  it('blocks submission and shows inline errors when required fields are empty', async () => {
    render(<ContactForm />);
    fireEvent.click(screen.getByRole('button', { name: /send message/i }));
    await waitFor(() => expect(screen.getByText(/please tell me your name/i)).toBeInTheDocument());
    expect(screen.getByText(/an email lets me reply/i)).toBeInTheDocument();
    expect(submitContactMock).not.toHaveBeenCalled();
  });

  it('validates email format inline', async () => {
    render(<ContactForm />);
    const email = screen.getByLabelText('Email');
    fireEvent.change(email, { target: { value: 'nope' } });
    fireEvent.blur(email);
    await waitFor(() => expect(screen.getByText(/valid email/i)).toBeInTheDocument());
  });

  it('hides project/budget fields by default (progressive disclosure)', () => {
    // Default purpose is "general", so the hiring/collaboration-only fields
    // (project type + budget) must not be present. The reveal on purpose change
    // is driven by a Radix Select (exercised in e2e, not jsdom).
    render(<ContactForm />);
    expect(screen.queryByLabelText('Project type')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Budget (optional)')).not.toBeInTheDocument();
  });

  it('submits valid input with honeypot + timing and shows the success card', async () => {
    submitContactMock.mockResolvedValue({
      message: 'ok',
      reference: 'abc123def456',
      estimated_response: 'within 1-2 business days',
    });
    render(<ContactForm />);
    fill();
    fireEvent.click(screen.getByRole('button', { name: /send message/i }));

    await waitFor(() => expect(submitContactMock).toHaveBeenCalled());
    const payload = submitContactMock.mock.calls[0][0];
    expect(payload.name).toBe('Ada Lovelace');
    expect(payload.company_website).toBe(''); // honeypot empty
    expect(typeof payload.elapsed_ms).toBe('number');

    await waitFor(() => expect(screen.getByText(/message sent/i)).toBeInTheDocument());
    expect(screen.getByText('abc123def456')).toBeInTheDocument();
  });

  it('preserves input and shows an error banner when the API fails', async () => {
    const { ContactError } = await import('@/lib/api/contact');
    submitContactMock.mockRejectedValue(new ContactError(500, 'Server error - please retry.'));
    render(<ContactForm />);
    fill();
    fireEvent.click(screen.getByRole('button', { name: /send message/i }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/server error/i));
    // Input is preserved (not reset) so the user can retry.
    expect(screen.getByLabelText('Your name')).toHaveValue('Ada Lovelace');
    expect(screen.getByLabelText('Message')).toHaveValue(
      'I would love to collaborate on your tailoring engine.'
    );
  });
});
