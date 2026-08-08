import { useState } from 'react';

import type { Redline, RiskFlag, Severity } from '../api/types';
import { ConfidenceBar, SeverityBadge, StatusBadge } from '../components/Badges';
import { AlertIcon } from '../components/Icons';
import { TraceTimeline } from '../components/TraceTimeline';
import {
  useConfirmedEdges,
  useRedlines,
  useReviewQueue,
  useRiskFlags,
} from '../hooks/useReviewQueue';

const SEVERITY_ORDER: Record<Severity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

export function Dashboard() {
  const [documentId, setDocumentId] = useState('DOC-001');

  const risks = useRiskFlags(documentId);
  const redlines = useRedlines(documentId);
  const edges = useConfirmedEdges(documentId);
  const queue = useReviewQueue({ statuses: ['pending_review'], documentId });

  const sortedRisks = [...(risks.data ?? [])].sort(
    (a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity],
  );
  const openRisks = sortedRisks.filter((r) => r.status === 'flagged').length;

  return (
    <section>
      <header className="page-head">
        <h1>Compliance &amp; risk</h1>
        <label className="doc-picker">
          <span className="field-label">Document</span>
          <input value={documentId} onChange={(e) => setDocumentId(e.target.value)} />
        </label>
      </header>

      <div className="stats">
        <Stat label="Open risk flags" value={risks.data ? openRisks : undefined} />
        <Stat label="Confirmed KG edges" value={edges.data?.length} />
        <Stat label="Suggested redlines" value={redlines.data?.length} />
        <Stat label="Awaiting review" value={queue.data?.length} />
      </div>

      {edges.isError ? (
        <p className="panel panel-error" role="alert">
          <AlertIcon size={15} /> Knowledge graph unavailable:{' '}
          {(edges.error as Error).message}. Has the pipeline been run with{' '}
          <code>--persist</code>?
        </p>
      ) : null}

      <section className="panel stub">
        <h2>
          Risk flags <span className="stub-tag">stub — Dev 4</span>
        </h2>
        {risks.isError ? (
          <p className="panel panel-error" role="alert">
            <AlertIcon size={15} /> Risk flags unavailable:{' '}
            {(risks.error as Error).message}
          </p>
        ) : sortedRisks.length === 0 ? (
          <p className="muted">No flags for {documentId}.</p>
        ) : (
          <ul className="cards">
            {sortedRisks.map((risk) => (
              <RiskCard key={risk.id} risk={risk} />
            ))}
          </ul>
        )}
      </section>

      <section className="panel stub">
        <h2>
          Suggested redlines <span className="stub-tag">stub — Redline Generator</span>
        </h2>
        {redlines.isError ? (
          <p className="panel panel-error" role="alert">
            <AlertIcon size={15} /> Redlines unavailable:{' '}
            {(redlines.error as Error).message}
          </p>
        ) : (redlines.data ?? []).length === 0 ? (
          <p className="muted">No redlines for {documentId}.</p>
        ) : (
          <ul className="cards">
            {redlines.data?.map((redline) => (
              <RedlineCard key={redline.redline_id} redline={redline} />
            ))}
          </ul>
        )}
      </section>
    </section>
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

function RiskCard({ risk }: { risk: RiskFlag }) {
  const suppressed = risk.status === 'suppressed';
  return (
    <li className={suppressed ? 'card card-suppressed' : 'card'}>
      <div className="card-head">
        <SeverityBadge severity={risk.severity} />
        <h3>
          Clause <code>{risk.clause_ref}</code> — <code>{risk.rule_id}</code>
        </h3>
        <span className="badge">{risk.status}</span>
      </div>
      <p className="provenance">
        Rule version <code>{risk.rule_version}</code>
        {risk.triggering_fact_ids.length > 0 ? (
          <> · triggered by {risk.triggering_fact_ids.join(', ')}</>
        ) : null}
      </p>
      {suppressed ? (
        <p className="muted">
          Suppressed — a confirmed edge
          {risk.suppressing_edge_id ? <> (<code>{risk.suppressing_edge_id}</code>)</> : null}{' '}
          waives this rule, so it is not a finding.
        </p>
      ) : null}
    </li>
  );
}

function RedlineCard({ redline }: { redline: Redline }) {
  return (
    <li className="card">
      <div className="card-head">
        <StatusBadge status={redline.status} />
        <code>{redline.clause_ref}</code>
        <ConfidenceBar value={redline.confidence} />
      </div>
      <p className="was">{redline.original_text}</p>
      <p className="now">{redline.suggested_text}</p>
      <p>{redline.rationale}</p>
      <details>
        <summary>Retrieval trace ({redline.rounds_attempted} rounds)</summary>
        <TraceTimeline trace={redline.trace} source="redline_generator" />
      </details>
    </li>
  );
}
