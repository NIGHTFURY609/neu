import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { EscalationItem } from '../api/types';
import { EscalationDetail } from './EscalationDetail';

const ITEM: EscalationItem = {
  id: 'ESC-DOC-001-C011-E01',
  target_edge_id: 'DOC-001-C011-E01',
  target_redline_id: null,
  status: 'pending_review',
  source: 'clause_ner',
  reason: 'ambiguous_edge',
  document_id: 'DOC-001',
  clause_ref: '8.2',
  rounds_attempted: 3,
  trace: [
    { round: 1, attempt: 'kg_precedent', result: 'No precedent found.', resolved: false },
  ],
  reviewer_id: null,
  resolved_at: null,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/queue/${ITEM.id}`]}>
        <Routes>
          <Route path="/queue/:id" element={<EscalationDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('EscalationDetail resolve flow', () => {
  it('sends the reviewer’s decision and the edge type they pinned', async () => {
    const resolved = { ...ITEM, status: 'confirmed' as const, reviewer_id: 'ada' };
    fetchMock
      .mockResolvedValueOnce(json(ITEM))
      // The POST, then the refetch `invalidateQueries` kicks off after it. A fresh
      // Response per call — a body can only be read once.
      .mockImplementation(() => Promise.resolve(json(resolved)));

    renderDetail();
    await screen.findByText(/Agent tried 3 rounds/i);

    await userEvent.type(screen.getByLabelText(/reviewer id/i), 'ada');
    await userEvent.selectOptions(screen.getByLabelText(/correct relationship/i), 'WAIVES');
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }));

    const post = await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([, init]) => (init as RequestInit | undefined)?.method === 'POST',
      );
      if (!call) throw new Error('no POST yet');
      return call as [string, RequestInit];
    });

    expect(post[0]).toBe(`/api/review-queue/${ITEM.id}/resolve`);
    expect(JSON.parse(String(post[1].body))).toEqual({
      status: 'confirmed',
      reviewer_id: 'ada',
      edge_type: 'WAIVES',
    });
    expect(await screen.findByText(/recorded as confirmed/i)).toBeInTheDocument();
  });

  it('refuses to submit an unattributed resolution', async () => {
    fetchMock.mockResolvedValueOnce(json(ITEM));

    renderDetail();
    await screen.findByText(/Agent tried 3 rounds/i);
    // Whitespace satisfies the input's `required` attribute but is not an attribution.
    await userEvent.type(screen.getByLabelText(/reviewer id/i), '   ');
    await userEvent.click(screen.getByRole('button', { name: /reject/i }));

    expect(await screen.findByText(/reviewer id is required/i)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('surfaces a 409 as "someone else got there first"', async () => {
    fetchMock
      .mockResolvedValueOnce(json(ITEM))
      .mockResolvedValueOnce(
        json({ detail: 'Escalation was already confirmed by bob' }, 409),
      );

    renderDetail();
    await screen.findByText(/Agent tried 3 rounds/i);

    await userEvent.type(screen.getByLabelText(/reviewer id/i), 'ada');
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }));

    expect(await screen.findByText(/already confirmed by bob/i)).toBeInTheDocument();
    expect(screen.getByText(/reload to see their decision/i)).toBeInTheDocument();
  });

  it('follows target_redline_id through to the redline it holds', async () => {
    // Shape of `fixtures/escalation.redline.json`: a redline target, no edge target.
    const escalation = {
      ...ITEM,
      id: 'ESC-RL-DOC-001-002',
      target_edge_id: null,
      target_redline_id: 'RL-DOC-001-002',
      source: 'redline_generator',
      reason: 'low_confidence',
      clause_ref: '2.4',
    };
    const redline = {
      redline_id: 'RL-DOC-001-002',
      document_id: 'DOC-001',
      risk_id: 'RISK-DOC-001-002',
      clause_ref: '2.4',
      status: 'pending_review',
      confidence: 0.71,
      original_text: 'was',
      suggested_text: 'now',
      rationale: 'because',
      rounds_attempted: 2,
      trace: [],
    };
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(json(url.includes('/redlines/') ? redline : escalation)),
    );

    renderDetail();

    expect(await screen.findByText('RL-DOC-001-002')).toBeInTheDocument();
    expect(await screen.findByText(/confidence 0\.71/i)).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([url]) => url)).toContain('/api/redlines/RL-DOC-001-002');
  });

  it('says so plainly when an escalation targets nothing at all', async () => {
    // `budget_exhausted` deliberately writes no redline row, so neither target is set.
    fetchMock.mockResolvedValueOnce(
      json({ ...ITEM, target_edge_id: null, reason: 'budget_exhausted' }),
    );

    renderDetail();

    expect(await screen.findByText(/exhausted its budget/i)).toBeInTheDocument();
  });

  it('offers no resolve form once the item is already decided', async () => {
    fetchMock.mockResolvedValueOnce(
      json({ ...ITEM, status: 'rejected', reviewer_id: 'bob' }),
    );

    renderDetail();

    expect(await screen.findByText(/already rejected/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /confirm/i })).not.toBeInTheDocument();
  });
});
