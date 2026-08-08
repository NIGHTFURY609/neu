import type { DocumentStatus } from '../api/types';

/**
 * The four pipeline stages as an ordered rail.
 *
 * Reuses `.trace` — the component that already renders "an ordered sequence of attempts
 * where you can see which one resolved and where it stopped". That is exactly what
 * pipeline progress is, so this needs one new class (a pulse on the running node) and no
 * new visual vocabulary at all.
 *
 * The backend reports a single current `stage` rather than per-stage records, so position
 * is derived from where that stage sits in the ordered list. A stage before the current
 * one has necessarily completed — the chain is sequential and stops at its first failure.
 */

const STAGES: Array<{ key: string; label: string; detail: string }> = [
  { key: 'ingestion', label: 'Ingest', detail: 'OCR, chunking, embedding' },
  { key: 'clause_ner', label: 'Extract', detail: 'Clauses, facts, knowledge graph' },
  { key: 'risk', label: 'Risk', detail: 'Playbook rules and waivers' },
  { key: 'redline', label: 'Redline', detail: 'Grounded rewrites' },
];

type StageState = 'done' | 'running' | 'failed' | 'pending';

function stateOf(index: number, status: DocumentStatus | undefined): StageState {
  if (status === undefined) return 'pending';
  if (status.status === 'succeeded') return 'done';

  // `stage` is null once finished and, on the queued state, before anything starts.
  const current = status.stage ? STAGES.findIndex((s) => s.key === status.stage) : -1;
  if (status.status === 'failed') {
    if (current === -1) return index === 0 ? 'failed' : 'pending';
    if (index < current) return 'done';
    return index === current ? 'failed' : 'pending';
  }
  if (status.status === 'queued' || current === -1) return 'pending';
  if (index < current) return 'done';
  return index === current ? 'running' : 'pending';
}

export function StageProgress({ status }: { status: DocumentStatus | undefined }) {
  const states = STAGES.map((_, index) => stateOf(index, status));
  const activeIndex = states.findIndex((s) => s === 'running' || s === 'failed');
  const counts = status?.summary ?? {};

  return (
    <div role="status" aria-live="polite">
      <p className="muted">
        {status === undefined
          ? 'Waiting for the pipeline to start.'
          : status.status === 'succeeded'
            ? 'All four stages finished.'
            : status.status === 'failed'
              ? `Stopped at ${STAGES[activeIndex]?.label ?? 'the first stage'}.`
              : `Stage ${activeIndex + 1 || 1} of ${STAGES.length} — ${
                  STAGES[activeIndex]?.label ?? 'queued'
                }.`}
      </p>

      <ol className="trace">
        {STAGES.map((stage, index) => {
          const state = states[index] ?? 'pending';
          return (
            <li
              key={stage.key}
              className={[
                'trace-round',
                state === 'done' ? 'is-resolved' : '',
                state === 'running' ? 'is-running' : '',
                state === 'failed' ? 'is-failed' : '',
              ]
                .filter(Boolean)
                .join(' ')}
            >
              <span className="trace-round-number">Stage {index + 1}</span>
              <span className="trace-attempt-label">{stage.label}</span>
              <span className="trace-result">
                {state === 'failed' && status?.error ? status.error : stage.detail}
              </span>
              <span className="mono">{summaryFor(stage.key, counts)}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function summaryFor(key: string, counts: Record<string, number>): string {
  switch (key) {
    case 'clause_ner': {
      const facts = counts.facts ?? 0;
      const edges = counts.confirmed_edges ?? 0;
      return facts || edges ? `${facts} facts · ${edges} edges` : '';
    }
    case 'risk':
      return counts.risk_flags ? `${counts.risk_flags} flags` : '';
    case 'redline':
      return counts.redlines ? `${counts.redlines} redlines` : '';
    default:
      return '';
  }
}
