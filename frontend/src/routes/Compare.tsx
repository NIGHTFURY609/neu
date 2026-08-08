import { useSearchParams } from 'react-router-dom';

import type { ClauseAlignment, DiffSpan } from '../api/types';
import { RiskDeltaList } from '../components/RiskDeltaList';
import { AlertIcon } from '../components/Icons';
import { useComparison, useDocuments } from '../hooks/useDocuments';

/**
 * Two documents side by side — but the risk delta first, because that is the answer to
 * the question a reviewer actually asked. The text diff is corroboration.
 */
export function Compare() {
  const [params, setParams] = useSearchParams();
  const left = params.get('left');
  const right = params.get('right');

  const { data: documents } = useDocuments();
  const { data, isPending, isError, error } = useComparison(left, right);

  const setSide = (side: 'left' | 'right', value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(side, value);
    else next.delete(side);
    setParams(next);
  };

  return (
    <>
      <header className="page-head">
        <h1>Compare</h1>
        <p className="muted">
          Two versions of one contract, or two unrelated documents. Which of the two it is
          gets decided by the data, not by you.
        </p>
      </header>

      <div className="filters">
        {(['left', 'right'] as const).map((side) => (
          <label key={side}>
            <span className="field-label">{side === 'left' ? 'Baseline' : 'Compared to'}</span>
            <select value={params.get(side) ?? ''} onChange={(e) => setSide(side, e.target.value)}>
              <option value="">Choose a document</option>
              {(documents ?? []).map((doc) => (
                <option key={doc.document_id} value={doc.document_id}>
                  {doc.title ?? doc.filename} (v{doc.version})
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>

      {left === null || right === null ? (
        <div className="panel-empty">Pick two documents to compare.</div>
      ) : left === right ? (
        <div className="panel-empty">Those are the same document.</div>
      ) : isError ? (
        <section className="panel panel-error">
          <h2>Could not compare</h2>
          <p>{(error as Error).message}</p>
        </section>
      ) : isPending ? (
        <div className="skeleton" />
      ) : data ? (
        <>
          <p className="muted">
            {data.pairing === 'version'
              ? `Versions of one contract: v${data.left.version} against v${data.right.version}.`
              : 'Two unrelated documents — clause numbering is aligned by content, not by number.'}
          </p>

          {data.caveats.length > 0 ? (
            <section className="panel panel-error">
              <h2>
                <AlertIcon size={15} /> Read this first
              </h2>
              <ul>
                {data.caveats.map((caveat) => (
                  <li key={caveat}>{caveat}</li>
                ))}
              </ul>
            </section>
          ) : null}

          <div className="stats">
            <Stat label="New findings" value={data.totals.risks_appeared} />
            <Stat label="Severity changed" value={data.totals.risks_severity_changed} />
            <Stat label="Resolved" value={data.totals.risks_resolved} />
            <Stat label="Clauses changed" value={data.totals.clauses_modified} />
          </div>

          <section className="panel">
            <h2>Risk delta</h2>
            <RiskDeltaList delta={data.risk_deltas} />
          </section>

          {data.fact_deltas.some((f) => f.change !== 'unchanged') ? (
            <section className="panel">
              <h2>What the numbers say</h2>
              <ul className="cards">
                {data.fact_deltas
                  .filter((f) => f.change !== 'unchanged')
                  .map((fact, index) => (
                    <li className="card" key={`${fact.fact_type}-${index}`}>
                      <div className="card-head">
                        <span className="mono">{fact.fact_type}</span>
                        <code>{fact.right_ref ?? fact.left_ref ?? '—'}</code>
                      </div>
                      <p className="was">{format(fact.left_value)}</p>
                      <p className="now">{format(fact.right_value)}</p>
                    </li>
                  ))}
              </ul>
            </section>
          ) : null}

          {data.redline_outcomes.length > 0 ? (
            <section className="panel">
              <h2>Did our redlines land?</h2>
              <ul className="cards">
                {data.redline_outcomes.map((outcome) => (
                  <li
                    className={
                      outcome.outcome === 'not_applied' || outcome.outcome === 'clause_removed'
                        ? 'card card-suppressed'
                        : 'card'
                    }
                    key={outcome.redline_id}
                  >
                    <div className="card-head">
                      <code>{outcome.clause_ref}</code>
                      <span className="pill">{outcome.outcome.replace(/_/g, ' ')}</span>
                      <span className="mono">{Math.round(outcome.similarity * 100)}%</span>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <section className="panel">
            <h2>Text</h2>
            <p className="muted">
              Unified rather than side by side: at this width in a monospace face two
              columns give about 45 characters each, which is unreadable for contract
              prose.
            </p>
            {data.clauses
              .filter((clause) => clause.status !== 'identical')
              .map((clause) => (
                <ClauseDiff key={`${clause.left_ref ?? ''}-${clause.right_ref ?? ''}`} clause={clause} />
              ))}
          </section>
        </>
      ) : null}
    </>
  );
}

function ClauseDiff({ clause }: { clause: ClauseAlignment }) {
  return (
    <article className={clause.status === 'removed' ? 'card card-suppressed' : 'card'}>
      <div className="card-head">
        <code>
          {clause.left_ref ?? '—'} → {clause.right_ref ?? '—'}
        </code>
        <span className="pill">{clause.status}</span>
        {/* A low pairing score means the machine guessed at this alignment. Saying so is
            cheaper than having a reviewer discover it by confusion. */}
        {clause.alignment_score > 0 && clause.alignment_score < 0.7 ? (
          <span className="badge badge-pending_review">uncertain pairing</span>
        ) : null}
      </div>
      {clause.diff ? (
        <p className="diff-body mono">
          {clause.diff.map((span, index) => (
            <DiffPiece key={index} span={span} />
          ))}
        </p>
      ) : null}
    </article>
  );
}

function DiffPiece({ span }: { span: DiffSpan }) {
  if (span.op === 'equal') return <span>{span.left}</span>;
  return (
    <>
      {span.left ? <span className="diff-del">{span.left}</span> : null}
      {span.right ? <span className="diff-ins">{span.right}</span> : null}
    </>
  );
}

function format(value: Record<string, unknown> | null): string {
  if (value === null) return '—';
  const preferred = value.amount_text ?? value.amount ?? value.jurisdiction ?? value.name;
  if (preferred !== undefined) return String(preferred);
  return JSON.stringify(value);
}

function Stat({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="stat">
      <span className="stat-value">{value ?? '—'}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}
