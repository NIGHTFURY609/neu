import { useMemo, useState } from 'react';

import { REVIEW_STATUSES, type EscalationSource, type ReviewStatus } from '../api/types';
import { EscalationRow } from '../components/EscalationRow';
import { AlertIcon, CheckIcon } from '../components/Icons';
import { useReviewQueue } from '../hooks/useReviewQueue';

const SOURCES: { value: EscalationSource | ''; label: string }[] = [
  { value: '', label: 'Both sources' },
  { value: 'clause_ner', label: 'Clause NER' },
  { value: 'redline_generator', label: 'Redline Generator' },
];

export function ReviewQueue() {
  const [statuses, setStatuses] = useState<ReviewStatus[]>(['pending_review']);
  const [documentId, setDocumentId] = useState('');
  const [source, setSource] = useState<EscalationSource | ''>('');

  const filters = useMemo(
    () => ({
      statuses,
      documentId: documentId.trim() || undefined,
      source: source || undefined,
    }),
    [statuses, documentId, source],
  );

  const { data, isPending, isError, error } = useReviewQueue(filters);

  const toggleStatus = (status: ReviewStatus) => {
    setStatuses((current) =>
      current.includes(status)
        ? current.filter((s) => s !== status)
        : [...current, status],
    );
  };

  return (
    <section>
      <header className="page-head">
        <h1>Review queue</h1>
        <p className="page-sub">
          Escalations from Clause NER and the Redline Generator. Both land here; both
          show what the agent tried before asking.
        </p>
      </header>

      <form className="filters" onSubmit={(e) => e.preventDefault()}>
        <fieldset>
          <legend>Status</legend>
          <div className="chips">
            {REVIEW_STATUSES.map((status) => (
              <label key={status} className="chip">
                <input
                  type="checkbox"
                  checked={statuses.includes(status)}
                  onChange={() => toggleStatus(status)}
                />
                <CheckIcon size={12} className="chip-tick" />
                {status}
              </label>
            ))}
          </div>
        </fieldset>

        <label>
          <span className="field-label">Document</span>
          <input
            type="search"
            value={documentId}
            placeholder="DOC-001"
            onChange={(e) => setDocumentId(e.target.value)}
          />
        </label>

        <label>
          <span className="field-label">Source</span>
          <select
            value={source}
            onChange={(e) => setSource(e.target.value as EscalationSource | '')}
          >
            {SOURCES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </form>

      {isPending ? (
        <div className="panel" aria-busy="true" aria-label="Loading queue">
          <span className="skeleton" />
          <span className="skeleton" />
          <span className="skeleton" />
        </div>
      ) : null}

      {isError ? (
        <p className="panel panel-error" role="alert">
          <AlertIcon size={15} /> Could not load the queue: {(error as Error).message}
        </p>
      ) : null}

      {data && data.length === 0 ? (
        <p className="panel panel-empty">
          Nothing waiting. Either the pipeline has not run, or every ambiguity it hit
          resolved itself within the retry budget.
        </p>
      ) : null}

      {data && data.length > 0 ? (
        <div className="table-wrap">
          <table className="queue">
            <caption>
              {data.length} {data.length === 1 ? 'item' : 'items'}
            </caption>
            <thead>
              <tr>
                <th scope="col">Escalation</th>
                <th scope="col">Document</th>
                <th scope="col">Clause</th>
                <th scope="col">Flagged for</th>
                <th scope="col">Reason</th>
                <th scope="col">Attempted</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.map((item) => (
                <EscalationRow key={item.id} item={item} />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
