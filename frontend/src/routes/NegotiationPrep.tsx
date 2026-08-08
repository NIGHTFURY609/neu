import { useParams } from 'react-router-dom';

import type { NegotiationLadder, NegotiationPosition } from '../api/types';
import { SeverityBadge } from '../components/Badges';
import { useNegotiationLadders } from '../hooks/useDocuments';

/**
 * How far can we concede, and what does each step cost?
 *
 * Every rung is derived from the playbook — the rule's standard language, the overrides
 * it permits, and the counterparty's own text — so nothing here is a model's opinion
 * about what a reasonable compromise looks like. That is what makes it printable and
 * defensible: each position traces to a versioned rule.
 */
export function NegotiationPrep() {
  const { documentId = null } = useParams();
  const { data, isPending, isError, error } = useNegotiationLadders(documentId);

  if (isError) {
    return (
      <section className="panel panel-error">
        <h2>Could not load negotiation positions</h2>
        <p>{(error as Error).message}</p>
      </section>
    );
  }
  if (isPending) return <div className="skeleton" />;

  const ladders = data ?? [];
  const noFallback = ladders.filter((l) => l.no_fallback_available).length;
  const positions = ladders.reduce((sum, l) => sum + l.positions.length, 0);

  return (
    <>
      <header className="page-head">
        <h1>Negotiation prep</h1>
        <div className="actions">
          <button type="button" className="secondary" onClick={() => window.print()}>
            Print
          </button>
        </div>
      </header>

      <div className="stats">
        <Stat label="Clauses" value={ladders.length} />
        <Stat label="Positions" value={positions} />
        {/* The most useful number on the page, and free to compute: where the playbook
            sanctions no concession at all. */}
        <Stat label="No fallback permitted" value={noFallback} />
      </div>

      {ladders.length === 0 ? (
        <div className="panel-empty">
          No flagged clauses with redlines yet — run the pipeline on this document first.
        </div>
      ) : (
        <ul className="cards">
          {ladders.map((ladder) => (
            <PositionLadder key={ladder.redline_id} ladder={ladder} />
          ))}
        </ul>
      )}
    </>
  );
}

function PositionLadder({ ladder }: { ladder: NegotiationLadder }) {
  return (
    <li className="card">
      <div className="card-head">
        <code>{ladder.clause_ref}</code>
        <SeverityBadge severity={ladder.severity} />
        <span className="mono">
          {ladder.rule_id} v{ladder.rule_version}
        </span>
      </div>

      {ladder.no_fallback_available ? (
        <p className="muted">
          This rule permits no override, so there is no middle position. Anything below
          the preferred language is a walk-away.
        </p>
      ) : null}

      {/* The `.trace` rail again — it already means "an ordered sequence where you can
          see how far down it went", which is exactly a concession ladder. */}
      <ol className="trace">
        {ladder.positions.map((position) => (
          <Rung key={position.position_id} position={position} />
        ))}
      </ol>
    </li>
  );
}

function Rung({ position }: { position: NegotiationPosition }) {
  const className = [
    'trace-round',
    position.tier === 'preferred' ? 'is-resolved' : '',
    position.tier === 'walk_away' ? 'ladder-walk-away' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <li className={className}>
      <span className="trace-round-number">{position.rank}</span>
      <span className="trace-attempt-label">
        <span className="pill">{position.tier.replace(/_/g, ' ')}</span>{' '}
        <SeverityBadge severity={position.residual_severity} />
        {position.grounded_in_override ? (
          <code>{position.grounded_in_override}</code>
        ) : null}
      </span>
      <span className="trace-result">
        <span className="now">{position.suggested_text}</span>
        <em className="muted">{position.concession}</em>
      </span>
    </li>
  );
}

function Stat({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="stat">
      <span className="stat-value">{value ?? '—'}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}
