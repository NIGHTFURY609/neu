import { Link } from 'react-router-dom';

import type { DocumentSummary, Severity } from '../../api/types';
import { ChevronRightIcon } from '../Icons';
import { JurisdictionBadge } from '../Badges';

const COLUMNS: Severity[] = ['critical', 'high', 'medium', 'low'];
const WEIGHT: Record<Severity, number> = { critical: 4, high: 3, medium: 2, low: 1 };

export function attentionScore(document: DocumentSummary): number {
  return COLUMNS.reduce(
    (total, severity) => total + WEIGHT[severity] * (document.risk_counts[severity] ?? 0),
    0,
  );
}

/**
 * Documents down, severities across, count in each cell.
 *
 * A heat map with no hue, so intensity is carried by an opacity ramp on `--text` — and
 * the count is *printed inside every cell*, which means the table stays fully readable
 * if the shading is invisible to you for any reason. The shading is a scanning aid, not
 * the data.
 *
 * Doubles as the "needs attention" panel: pass `limit` and it becomes the top N by
 * attention score. One component, two jobs, one sort order.
 */
export function RiskMatrix({
  documents,
  limit,
}: {
  documents: DocumentSummary[];
  limit?: number;
}) {
  const ranked = [...documents].sort((a, b) => attentionScore(b) - attentionScore(a));
  const rows = limit ? ranked.slice(0, limit) : ranked;

  if (rows.length === 0) {
    return <div className="panel-empty">No documents yet.</div>;
  }

  const peak = Math.max(
    1,
    ...rows.flatMap((doc) => COLUMNS.map((severity) => doc.risk_counts[severity] ?? 0)),
  );

  return (
    <div className="table-wrap">
      <table className="queue matrix">
        <thead>
          <tr>
            <th scope="col">Document</th>
            {COLUMNS.map((severity) => (
              <th key={severity} scope="col" className="matrix-head">
                {severity}
              </th>
            ))}
            <th scope="col" className="matrix-head">
              queue
            </th>
            <th scope="col" aria-label="Open" />
          </tr>
        </thead>
        <tbody>
          {rows.map((doc) => (
            <tr key={doc.document_id}>
              <td>
                <Link to={`/documents/${doc.document_id}`} className="row-id">
                  {doc.title ?? doc.filename}
                </Link>{' '}
                <JurisdictionBadge code={doc.jurisdiction} />
                {doc.version > 1 ? <span className="pill mono">v{doc.version}</span> : null}
              </td>
              {COLUMNS.map((severity) => {
                const count = doc.risk_counts[severity] ?? 0;
                return (
                  <td key={severity} className="matrix-cell">
                    <span
                      className="matrix-ink"
                      // Five visual steps rather than a continuous ramp: adjacent
                      // continuous values are indistinguishable in grey anyway.
                      style={{ opacity: count === 0 ? 0 : 0.2 + 0.8 * (count / peak) }}
                      aria-hidden="true"
                    />
                    <span className="mono matrix-count">{count || '—'}</span>
                  </td>
                );
              })}
              <td className="matrix-cell">
                <span className="mono matrix-count">{doc.open_escalations || '—'}</span>
              </td>
              <td className="row-go">
                <Link to={`/documents/${doc.document_id}`} aria-label={`Open ${doc.filename}`}>
                  <ChevronRightIcon />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
