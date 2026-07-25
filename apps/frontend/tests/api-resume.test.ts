import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  confirmImproveResume,
  generateInterviewPrep,
  recoverTailorPreview,
  streamImproveResume,
  TailorStreamCancelled,
  TailorStreamError,
} from '@/lib/api/resume';

const interviewPrep = {
  role_fit_analysis: ['Backend API experience fits the role.'],
  resume_questions: [
    {
      question: 'How did you build the API?',
      focus_area: 'Backend architecture',
      suggested_answer_points: ['Discuss resume-grounded API work.'],
    },
  ],
  project_follow_ups: [],
  skill_gaps: [
    {
      skill: 'Kubernetes',
      why_it_matters: 'The JD mentions deployments.',
      preparation_suggestion: 'Review basics without claiming experience.',
    },
  ],
  talking_points: ['Connect API work to the role.'],
};

describe('resume API', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('generates interview prep and parses the structured payload', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          interview_prep: interviewPrep,
          message: 'Interview preparation generated successfully',
        }),
        { status: 200 }
      )
    );

    await expect(generateInterviewPrep('res 123')).resolves.toEqual(interviewPrep);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/resumes/res%20123/generate-interview-prep');
    expect(init.method).toBe('POST');
  });

  it('throws a useful error when interview prep generation fails', async () => {
    fetchMock.mockResolvedValue(new Response('server boom', { status: 500 }));

    await expect(generateInterviewPrep('res-123')).rejects.toThrow(
      'Failed to generate interview preparation'
    );
  });

  it('sends the durable preview id when confirming a tailored resume', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ data: { preview_id: 'preview-1' } }), { status: 200 })
    );

    await confirmImproveResume({
      preview_id: 'preview-1',
      resume_id: 'resume-1',
      job_id: 'job-1',
      improved_data: {} as never,
      improvements: [],
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/resumes/improve/confirm');
    expect(JSON.parse(init.body)).toMatchObject({
      preview_id: 'preview-1',
      resume_id: 'resume-1',
      job_id: 'job-1',
    });
  });
});

describe('tailoring stream failure classification', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function runStream(signal = new AbortController().signal) {
    return streamImproveResume('resume-1', 'job-1', undefined, {
      requestId: 'request-1',
      signal,
    });
  }

  it('marks an open transport failure before any SSE event as safe to fall back', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

    const error = await runStream().catch((caught) => caught);
    expect(error).toBeInstanceOf(TailorStreamError);
    expect(error).toMatchObject({
      code: 'stream_transport_error',
      status: 0,
      phase: 'open',
      fallbackSafe: true,
    });
  });

  it.each(['streaming_disabled', 'streaming_unsupported'])(
    'marks a 409 %s capability response as safe to fall back',
    async (code) => {
      fetchMock.mockResolvedValue(
        new Response(JSON.stringify({ error: { code, message: 'Streaming is unavailable.' } }), {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        })
      );

      const error = await runStream().catch((caught) => caught);
      expect(error).toBeInstanceOf(TailorStreamError);
      expect(error).toMatchObject({
        code,
        status: 409,
        phase: 'open',
        fallbackSafe: true,
      });
    }
  );

  it.each([
    [401, 'not_authenticated'],
    [422, 'validation_error'],
    [429, 'rate_limited'],
  ])('does not allow fallback for HTTP %i (%s)', async (status, code) => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ error: { code, message: 'Request rejected.' } }), {
        status,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const error = await runStream().catch((caught) => caught);
    expect(error).toBeInstanceOf(TailorStreamError);
    expect(error).toMatchObject({ code, status, phase: 'open', fallbackSafe: false });
  });

  it('does not allow fallback after a terminal error SSE event', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        'event: stage\ndata: {"stage":"keywords","status":"start"}\n\n' +
          'event: error\ndata: {"message":"rewrite failed"}\n\n',
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } }
      )
    );

    const error = await runStream().catch((caught) => caught);
    expect(error).toBeInstanceOf(TailorStreamError);
    expect(error).toMatchObject({
      code: 'stream_terminal_error',
      phase: 'after-event',
      fallbackSafe: false,
    });
  });

  it('does not allow fallback when the network fails after an SSE event', async () => {
    let bodyController!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        bodyController = controller;
        controller.enqueue(
          new TextEncoder().encode('event: stage\ndata: {"stage":"keywords","status":"start"}\n\n')
        );
      },
    });
    fetchMock.mockResolvedValue(
      new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    );

    const error = await streamImproveResume('resume-1', 'job-1', undefined, {
      requestId: 'request-1',
      signal: new AbortController().signal,
      onStage: () => bodyController.error(new Error('connection reset')),
    }).catch((caught) => caught);

    expect(error).toBeInstanceOf(TailorStreamError);
    expect(error).toMatchObject({
      code: 'stream_transport_error',
      phase: 'after-event',
      fallbackSafe: false,
    });
  });

  it('does not allow fallback when an observed stream ends without a result', async () => {
    fetchMock.mockResolvedValue(
      new Response('event: stage\ndata: {"stage":"keywords","status":"done"}\n\n', {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      })
    );

    const error = await runStream().catch((caught) => caught);
    expect(error).toBeInstanceOf(TailorStreamError);
    expect(error).toMatchObject({
      code: 'stream_incomplete',
      phase: 'after-event',
      fallbackSafe: false,
    });
  });

  it('classifies an aborted stream as cancellation, never fallback-safe failure', async () => {
    const controller = new AbortController();
    fetchMock.mockImplementation((_url, init: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init.signal?.addEventListener('abort', () =>
          reject(new DOMException('Aborted', 'AbortError'))
        );
      });
    });

    const pending = runStream(controller.signal);
    controller.abort();

    await expect(pending).rejects.toBeInstanceOf(TailorStreamCancelled);
  });
});

describe('tailoring result recovery', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('returns the durable completed preview without issuing generation', async () => {
    const payload = { request_id: 'request-1', data: { preview_id: 'preview-1' } };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(recoverTailorPreview('request-1', 1)).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/resumes/improve/preview/result/request-1');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'GET' });
  });

  it('returns null after the bounded number of not-ready attempts', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ error: { code: 'preview_result_not_ready', message: 'Not ready' } }),
          { status: 404, headers: { 'Content-Type': 'application/json' } }
        )
      );
    vi.stubGlobal('fetch', fetchMock);

    await expect(recoverTailorPreview('request-1', 1)).resolves.toBeNull();
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});
