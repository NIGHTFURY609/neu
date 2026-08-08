import { Link } from 'react-router-dom';
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
  const openRisks = sortedRisks.filter((risk) => risk.status === 'flagged').length;

  return (
    <section className="workspace-dashboard">
      <header className="workspace-hero">
        <div>
          <p className="workspace-kicker">WORKSPACE / ACTIVE REVIEW</p>
          <h1>Make the next<br />move <em>obvious.</em></h1>
          <p className="workspace-lede">Your contract signals, explained and ready for a decision.</p>
        </div>
        <div className="workspace-document-control">
          <span>ACTIVE DOCUMENT</span>
          <label>
            <input value={documentId} onChange={(event) => setDocumentId(event.target.value)} aria-label="Document ID" />
            <i>↗</i>
          </label>
          <small><b /> Analysis complete · source-linked</small>
        </div>
      </header>

      <section className="workspace-metrics" aria-label="Document intelligence summary">
        <Metric label="Open risk flags" value={risks.data ? openRisks : undefined} detail="Need attention" accent />
        <Metric label="Confirmed links" value={edges.data?.length} detail="Knowledge graph" />
        <Metric label="Ready redlines" value={redlines.data?.length} detail="Cited suggestions" />
        <Metric label="Awaiting review" value={queue.data?.length} detail="Human decision" />
      </section>

      {edges.isError ? (
        <p className="panel panel-error" role="alert">
          <AlertIcon size={15} /> Knowledge graph unavailable: {(edges.error as Error).message}.
        </p>
      ) : null}

      <div className="workspace-overview">
        <section className="workspace-findings">
          <header className="workspace-section-head">
            <div><p className="workspace-kicker">PRIORITY FINDINGS</p><h2>What needs your eye.</h2></div>
            <span>{openRisks} open</span>
          </header>
          {risks.isError ? (
            <p className="panel panel-error" role="alert">
              <AlertIcon size={15} /> Risk flags unavailable: {(risks.error as Error).message}
            </p>
          ) : sortedRisks.length === 0 ? (
            <p className="workspace-empty">No risk flags for {documentId}.</p>
          ) : (
            <ul className="workspace-risk-list">
              {sortedRisks.map((risk, index) => <RiskCard key={risk.id} risk={risk} order={index + 1} />)}
            </ul>
          )}
        </section>

        <aside className="workspace-pulse">
          <p className="workspace-kicker">DOCUMENT PULSE</p>
          <div className="pulse-score"><small>EXPLAINABILITY</small><strong>100<span>%</span></strong><p>Every live finding includes a source, confidence, and rationale.</p></div>
          <div className="pulse-rows"><span><b>14</b> clauses analyzed</span><span><b>{edges.data?.length ?? '—'}</b> relationships confirmed</span><span><b>{queue.data?.length ?? '—'}</b> decisions waiting</span></div>
          <Link to="/queue" className="pulse-link">Open review queue <span>→</span></Link>
        </aside>
      </div>

      <section className="workspace-redlines">
        <header className="workspace-section-head">
          <div><p className="workspace-kicker">SUGGESTED LANGUAGE</p><h2>Redlines with receipts.</h2></div>
          <span>{redlines.data?.length ?? '—'} ready</span>
        </header>
        {redlines.isError ? (
          <p className="panel panel-error" role="alert">
            <AlertIcon size={15} /> Redlines unavailable: {(redlines.error as Error).message}
          </p>
        ) : (redlines.data ?? []).length === 0 ? (
          <p className="workspace-empty">No approved redlines for {documentId}.</p>
        ) : (
          <ul className="workspace-redline-list">
            {redlines.data?.map((redline) => <RedlineCard key={redline.redline_id} redline={redline} />)}
          </ul>
        )}
      </section>
    </section>
  );
}

function Metric({
  label,
  value,
  detail,
  accent = false,
}: {
  label: string;
  value: number | undefined;
  detail: string;
  accent?: boolean;
}) {
  return (
    <div className={`workspace-metric ${accent ? 'is-accent' : ''}`}>
      <span>{label}</span><strong>{value ?? '—'}</strong><small>{detail}</small>
    </div>
  );
}

function RiskCard({ risk, order }: { risk: RiskFlag; order: number }) {
  const suppressed = risk.status === 'suppressed';
  return (
    <li className={`workspace-risk ${suppressed ? 'is-suppressed' : ''}`}>
      <span className="risk-order">{String(order).padStart(2, '0')}</span>
      <div className="risk-copy">
        <div className="risk-meta"><SeverityBadge severity={risk.severity} /><span>CLAUSE {risk.clause_ref}</span></div>
        <h3>{risk.rule_id.replaceAll('-', ' ').toLowerCase()}</h3>
        <p>{suppressed ? 'A confirmed relationship overrides this rule, so it is recorded but does not require action.' : `Policy version ${risk.rule_version} identified a non-standard term in this clause.`}</p>
      </div>
      <div className="risk-evidence"><span>SOURCE</span><b>{risk.triggering_fact_ids[0] ?? 'Clause text'}</b></div>
      <span className={`risk-verdict ${suppressed ? 'is-suppressed' : ''}`}>{suppressed ? 'Cleared' : 'Review →'}</span>
    </li>
  );
}

function RedlineCard({ redline }: { redline: Redline }) {
  return (
    <li className="workspace-redline">
      <header><div><StatusBadge status={redline.status} /><span>CLAUSE {redline.clause_ref}</span></div><ConfidenceBar value={redline.confidence} /></header>
      <div className="redline-text"><section><small>CURRENT LANGUAGE</small><p>{redline.original_text}</p></section><span>→</span><section><small>SUGGESTED LANGUAGE</small><p>{redline.suggested_text}</p></section></div>
      <footer><p>{redline.rationale}</p><details><summary>View retrieval trace ({redline.rounds_attempted})</summary><TraceTimeline trace={redline.trace} source="redline_generator" /></details></footer>
    </li>
  );
}
