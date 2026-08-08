import type { EscalationReason, EscalationSource, TraceRound } from '../api/types';
import { ClockIcon, RetryIcon } from './Icons';

interface TraceTimelineProps {
  trace: TraceRound[];
  source: EscalationSource;
}

/**
 * The retry / retrieval trace.
 *
 * Two escalation sources feed one queue and this renders both. Clause NER's `attempt`
 * is a retry strategy, the Redline Generator's is the query it issued — same array,
 * same shape, so only the column heading changes. Do not branch on `source` for layout.
 *
 * `result` strings are written by the producing stage to be read by a human ("No
 * confirmed edge in this document matches the drafting pattern 'in_lieu_of'"), so they
 * are rendered verbatim.
 *
 * The rail down the left is the only visual state: a round that resolved gets a filled
 * node, the rest stay hollow, so where the loop stopped is readable without reading.
 */
export function TraceTimeline({ trace, source }: TraceTimelineProps) {
  if (trace.length === 0) {
    return (
      <p className="trace-empty">
        No trace — this was escalated immediately, without the agent attempting a
        resolution.
      </p>
    );
  }

  const attemptLabel = source === 'clause_ner' ? 'Strategy tried' : 'Query issued';

  return (
    <ol className="trace" aria-label={`${attemptLabel} by round`}>
      {trace.map((round) => (
        <li
          key={round.round}
          className={round.resolved ? 'trace-round is-resolved' : 'trace-round'}
        >
          <div className="trace-round-head">
            <span className="trace-round-number">Round {round.round}</span>
            <span className="trace-attempt-label">{attemptLabel}</span>
            <code className="trace-attempt">{round.attempt}</code>
            <span
              className={round.resolved ? 'pill pill-resolved' : 'pill pill-unresolved'}
            >
              {round.resolved ? 'resolved' : 'did not resolve'}
            </span>
          </div>
          <p className="trace-result">{round.result}</p>
        </li>
      ))}
    </ol>
  );
}

/**
 * The distinction the queue lives or dies on, surfaced before a reviewer opens
 * anything: has anyone — human or agent — actually looked at this yet?
 *
 * Weighted deliberately: the untouched case is dim and hollow-clocked, the burned-budget
 * case is full-brightness, because an item the agent already failed at is the expensive
 * one and should pull the eye first.
 */
const NO_TRACE_TRIGGER: Record<EscalationReason, string> = {
  ambiguous_edge: 'No confirmed reading — escalated before any retry ran.',
  budget_exhausted: 'No grounding found before the retrieval budget ran out.',
  low_confidence: 'Confidence stayed below threshold before any round completed.',
};

/**
 * What actually flagged this item, not which pipeline stage flagged it. The queue used
 * to show a `SourceBadge` here ("Clause NER" / "Redline Generator") — the internal name
 * of the code that ran, meaningless to a reviewer deciding whether to open the row. The
 * last trace round's `result` is the concrete, human-written explanation for the miss
 * (e.g. "readings are too close to separate"); with no trace at all, fall back to a
 * plain-language reading of why it was escalated immediately.
 */
export function TriggerText({ trace, reason }: { trace: TraceRound[]; reason: EscalationReason }) {
  const last = trace.at(-1);
  const text = last ? last.result : NO_TRACE_TRIGGER[reason];
  return (
    <span className="trigger-text" title={text}>
      {text}
    </span>
  );
}

export function AttemptSummary({ rounds }: { rounds: number }) {
  if (rounds === 0) {
    return (
      <span className="attempt attempt-none">
        <ClockIcon size={14} />
        Escalated immediately — not yet attempted
      </span>
    );
  }
  return (
    <span className="attempt attempt-tried">
      <RetryIcon size={14} />
      Attempted No. {rounds}
    </span>
  );
}
