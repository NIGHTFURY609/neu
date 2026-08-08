import { useState } from 'react';

import { RiskDeltaList } from './RiskDeltaList';
import { useRiskPreview } from '../hooks/useDocuments';

const JURISDICTIONS = ['US-NY', 'US-CA', 'US', 'EU', 'IN', 'GENERAL'];

/**
 * "How would this contract fare under another jurisdiction's playbook?"
 *
 * The backend re-runs the risk engine over the document's existing facts and confirmed
 * edges with a different rule set and returns the difference as `RiskDelta` — the same
 * type the comparison view uses, which is why `RiskDeltaList` renders both.
 *
 * Nothing is written. The stored risk flags stay the ones actually evaluated for this
 * document, so this can be run repeatedly without polluting the record.
 */
export function JurisdictionPreview({ documentId }: { documentId: string }) {
  const [target, setTarget] = useState<string | null>(null);
  const { data, isPending, isError, error } = useRiskPreview(documentId, target);

  return (
    <section className="panel">
      <h2>Under another jurisdiction</h2>
      <div className="filters">
        <label>
          <span className="field-label">Evaluate under</span>
          <select value={target ?? ''} onChange={(e) => setTarget(e.target.value || null)}>
            <option value="">Choose a jurisdiction</option>
            {JURISDICTIONS.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </select>
        </label>
      </div>

      {target === null ? (
        <div className="panel-empty">
          Pick a jurisdiction to re-evaluate this document against its playbook.
        </div>
      ) : isError ? (
        <p className="error">{(error as Error).message}</p>
      ) : isPending ? (
        <div className="skeleton" />
      ) : data ? (
        <>
          <p className="muted">
            {data.evaluated_rules} rule(s) apply in {data.target_jurisdiction}
            {data.base_jurisdiction ? ` (this document is filed under ${data.base_jurisdiction})` : ''}.
          </p>
          <RiskDeltaList delta={data.delta} />
          {data.unmapped_rules.length > 0 ? (
            // The line that keeps this honest. A tool claiming to have re-evaluated a
            // contract under a foreign regime without saying which of its rules have no
            // equivalent there is overstating what it did.
            <div className="panel-empty">
              {data.unmapped_rules.length} rule(s) that fired here have no{' '}
              {data.target_jurisdiction} equivalent and were not evaluated:{' '}
              <span className="mono">{data.unmapped_rules.join(', ')}</span>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
