import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { TraceRound } from '../api/types';
import { AttemptSummary, TraceTimeline } from './TraceTimeline';

const TRACE: TraceRound[] = [
  {
    round: 1,
    attempt: 'kg_precedent',
    result: "No confirmed edge in this document matches the drafting pattern 'in_lieu_of'.",
    resolved: false,
  },
  {
    round: 2,
    attempt: 'widen_context',
    result: 'Widened to Section 7.3, Section 1.1; still reads as OVERRIDES or WAIVES.',
    resolved: false,
  },
];

describe('TraceTimeline', () => {
  it('renders every round with the producing stage’s own wording', () => {
    render(<TraceTimeline trace={TRACE} source="clause_ner" />);

    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByText(/drafting pattern 'in_lieu_of'/)).toBeInTheDocument();
    expect(screen.getByText('kg_precedent')).toBeInTheDocument();
  });

  it('labels the attempt column by source without changing the layout', () => {
    const { unmount } = render(<TraceTimeline trace={TRACE} source="clause_ner" />);
    expect(screen.getAllByText('Strategy tried')).toHaveLength(2);
    unmount();

    render(<TraceTimeline trace={TRACE} source="redline_generator" />);
    expect(screen.getAllByText('Query issued')).toHaveLength(2);
    // Same element count either way — one component, two sources.
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
  });

  it('says so explicitly when there is no trace at all', () => {
    render(<TraceTimeline trace={[]} source="clause_ner" />);

    expect(screen.queryByRole('list')).not.toBeInTheDocument();
    expect(screen.getByText(/escalated immediately/i)).toBeInTheDocument();
  });
});

describe('AttemptSummary', () => {
  it('distinguishes "nobody looked yet" from "the agent tried and failed"', () => {
    const { unmount } = render(<AttemptSummary rounds={0} />);
    expect(screen.getByText(/not yet attempted/i)).toBeInTheDocument();
    unmount();

    render(<AttemptSummary rounds={3} />);
    expect(screen.getByText(/Attempted No\. 3/i)).toBeInTheDocument();
  });
});
