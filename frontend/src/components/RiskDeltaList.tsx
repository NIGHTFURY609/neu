import type { RiskDelta, RiskDeltaChange } from '../api/types';
import { SeverityBadge } from './Badges';
import { ChevronRightIcon } from './Icons';

const HEADINGS: Record<RiskDeltaChange, string> = {
  appeared: 'New findings',
  severity_changed: 'Severity changed',
  suppression_changed: 'Waiver changed',
  resolved: 'Resolved',
  unchanged: 'Unchanged',
};

// Most actionable first. `unchanged` is last and collapsed — it is the bulk of any real
// comparison and none of the signal.
const ORDER: RiskDeltaChange[] = [
  'appeared',
  'severity_changed',
  'suppression_changed',
  'resolved',
  'unchanged',
];

/**
 * Rendered by both the comparison view and the jurisdiction preview.
 *
 * That reuse is the reason `/compare` and `/documents/{id}/risk-preview` return the same
 * `RiskDelta` shape: "what changed between two versions" and "what would change under
 * another playbook" are the same question asked of a different second input.
 */
export function RiskDeltaList({ delta }: { delta: RiskDelta[] }) {
  if (delta.length === 0) {
    return <div className="panel-empty">No risk differences.</div>;
  }

  const grouped = new Map<RiskDeltaChange, RiskDelta[]>();
  for (const item of delta) {
    const bucket = grouped.get(item.change) ?? [];
    bucket.push(item);
    grouped.set(item.change, bucket);
  }

  return (
    <>
      {ORDER.filter((change) => grouped.has(change)).map((change) => {
        const items = grouped.get(change) ?? [];
        if (change === 'unchanged') {
          return (
            <details key={change} className="delta-unchanged">
              <summary>
                {items.length} finding{items.length === 1 ? '' : 's'} unchanged
              </summary>
              <ul className="cards">
                {items.map((item) => (
                  <DeltaCard key={`${item.rule_id}-${item.left_ref}`} delta={item} />
                ))}
              </ul>
            </details>
          );
        }
        return (
          <section key={change} className="delta-group">
            <h3>
              {HEADINGS[change]} <span className="pill mono">{items.length}</span>
            </h3>
            <ul className="cards">
              {items.map((item) => (
                <DeltaCard
                  key={`${item.rule_id}-${item.left_ref ?? ''}-${item.right_ref ?? ''}`}
                  delta={item}
                />
              ))}
            </ul>
          </section>
        );
      })}
    </>
  );
}

function DeltaCard({ delta }: { delta: RiskDelta }) {
  // `resolved` reuses `.card-suppressed` — the hatched, struck-through treatment that
  // already means "gone / dismissed" here. Semantically exact, and no new CSS.
  const className = delta.change === 'resolved' ? 'card card-suppressed' : 'card';
  return (
    <li className={className}>
      <div className="card-head">
        <code>{delta.right_ref ?? delta.left_ref ?? '—'}</code>
        <span className="mono">{delta.rule_id}</span>
        <span className="delta-severities">
          {delta.left_severity ? <SeverityBadge severity={delta.left_severity} /> : null}
          {delta.left_severity && delta.right_severity ? (
            <ChevronRightIcon size={13} />
          ) : null}
          {delta.right_severity ? <SeverityBadge severity={delta.right_severity} /> : null}
        </span>
      </div>
      {delta.note ? <p className="muted">{delta.note}</p> : null}
    </li>
  );
}
