import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { DOC_KINDS, type DocKind, type Severity } from '../api/types';
import { RiskMatrix } from '../components/charts/RiskMatrix';
import { SeverityRamp } from '../components/charts/SeverityRamp';
import { useDocuments } from '../hooks/useDocuments';
import { useReviewQueue } from '../hooks/useReviewQueue';

/**
 * Risk across every document, replacing the old document-scoped dashboard that opened on
 * a hardcoded `DOC-001`. The per-document view still exists at `/documents/:id` — this is
 * the layer above it.
 */
export function Portfolio() {
  const [docKind, setDocKind] = useState<DocKind | ''>('');
  const [jurisdiction, setJurisdiction] = useState('');

  const { data: documents, isPending, isError, error } = useDocuments({
    docKind: docKind || undefined,
    jurisdiction: jurisdiction || undefined,
  });
  const queue = useReviewQueue({ statuses: ['pending_review'] });

  const totals = useMemo(() => {
    const counts: Partial<Record<Severity, number>> = {};
    for (const doc of documents ?? []) {
      for (const [severity, count] of Object.entries(doc.risk_counts)) {
        counts[severity as Severity] = (counts[severity as Severity] ?? 0) + count;
      }
    }
    return counts;
  }, [documents]);

  const jurisdictions = useMemo(
    () => [...new Set((documents ?? []).map((d) => d.jurisdiction).filter(Boolean))] as string[],
    [documents],
  );

  const urgent = (totals.critical ?? 0) + (totals.high ?? 0);

  if (isError) {
    return (
      <section className="panel panel-error">
        <h2>Could not load the portfolio</h2>
        <p>{(error as Error).message}</p>
        <p className="muted">
          This is an error, not an empty portfolio. A backend outage must never render as
          a clean, risk-free set of documents.
        </p>
      </section>
    );
  }

  return (
    <>
      <header className="page-head">
        <h1>Compliance &amp; risk</h1>
        <p className="muted">Every document you can see, ranked by what needs attention.</p>
      </header>

      <div className="filters">
        <label>
          <span className="field-label">Kind</span>
          <select value={docKind} onChange={(e) => setDocKind(e.target.value as DocKind | '')}>
            <option value="">All</option>
            {DOC_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="field-label">Jurisdiction</span>
          <select value={jurisdiction} onChange={(e) => setJurisdiction(e.target.value)}>
            <option value="">All</option>
            {jurisdictions.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="stats">
        <Stat label="Documents" value={documents?.length} />
        <Stat label="Critical + high" value={isPending ? undefined : urgent} />
        <Stat label="Awaiting review" value={queue.data?.length} />
        <Stat
          label="Needing a run"
          value={
            documents?.filter((d) => d.processing_status !== 'succeeded').length
          }
        />
      </div>

      <section className="panel">
        <h2>Severity across the portfolio</h2>
        <SeverityRamp counts={totals} label="Open findings by severity" />
      </section>

      <section className="panel">
        <h2>Needs attention</h2>
        {isPending ? (
          <div className="skeleton" />
        ) : (
          <RiskMatrix documents={documents ?? []} limit={8} />
        )}
      </section>

      <section className="panel">
        <h2>All documents</h2>
        {isPending ? (
          <div className="skeleton" />
        ) : (documents ?? []).length === 0 ? (
          <div className="panel-empty">
            Nothing here yet. <Link to="/upload">Upload a document</Link> to populate it.
          </div>
        ) : (
          <RiskMatrix documents={documents ?? []} />
        )}
      </section>
    </>
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
