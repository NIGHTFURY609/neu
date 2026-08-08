import { useDeferredValue, useState } from 'react';
import { Link } from 'react-router-dom';

import { DOC_KINDS, type DocKind, type SearchHit } from '../api/types';
import { ConfidenceBar, JurisdictionBadge } from '../components/Badges';
import { useSearch } from '../hooks/useSearch';

export function Search() {
  const [query, setQuery] = useState('');
  const [docKind, setDocKind] = useState<DocKind | ''>('');
  // React 19, no timer to clean up and no debounce dependency: the deferred value lags
  // behind typing on its own and the query key changes only when it settles.
  const deferred = useDeferredValue(query);

  const { data, isPending, isError, error } = useSearch({
    q: deferred,
    docKind: docKind || undefined,
  });

  const ready = deferred.trim().length >= 2;

  return (
    <>
      <header className="page-head">
        <h1>Search</h1>
        <p className="muted">
          Clause numbers, quoted defined terms, or plain language. Searching{' '}
          <code>2.2</code> finds clause 2.2 itself, above the clauses that merely cite it.
        </p>
      </header>

      <div className="filters">
        <label style={{ flexGrow: 1 }}>
          <span className="field-label">Query</span>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder='liability cap, "Excluded Claims", Section 4.1'
            autoFocus
          />
        </label>
        <label>
          <span className="field-label">Corpus</span>
          <select value={docKind} onChange={(e) => setDocKind(e.target.value as DocKind | '')}>
            <option value="">Everything</option>
            {DOC_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
        </label>
      </div>

      {!ready ? (
        <div className="panel-empty">Type at least two characters.</div>
      ) : isError ? (
        <section className="panel panel-error">
          <h2>Search failed</h2>
          <p>{(error as Error).message}</p>
        </section>
      ) : isPending ? (
        <div className="skeleton" />
      ) : (data?.hits.length ?? 0) === 0 ? (
        <div className="panel-empty">
          Nothing matched <code>{data?.query}</code>.
        </div>
      ) : (
        <section className="panel">
          <h2>
            {data?.total} result{data?.total === 1 ? '' : 's'}{' '}
            <span className="pill mono">{data?.backend}</span>
          </h2>
          <ul className="cards">
            {(data?.hits ?? []).map((hit) => (
              <Hit key={hit.chunk_id} hit={hit} terms={data?.terms ?? []} />
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function Hit({ hit, terms }: { hit: SearchHit; terms: string[] }) {
  return (
    <li className="card">
      <div className="card-head">
        <Link to={`/documents/${hit.document_id}`} className="row-id">
          {hit.filename || hit.document_id}
        </Link>
        {hit.clause_ref ? <code>{hit.clause_ref}</code> : <span className="pill">preamble</span>}
        <JurisdictionBadge code={hit.jurisdiction} />
        <ConfidenceBar value={hit.score} />
      </div>
      <p>
        <Highlighted text={hit.snippet} terms={terms} />
      </p>
    </li>
  );
}

/**
 * Highlighting happens here rather than server-side: the snippet arrives as plain text,
 * so nothing ever needs `dangerouslySetInnerHTML`, and a future vector backend — which
 * has no tsquery to build a headline from — produces the same shape.
 */
function Highlighted({ text, terms }: { text: string; terms: string[] }) {
  const usable = terms.filter((t) => t.length > 1).map(escapeRegExp);
  if (usable.length === 0) return <>{text}</>;

  const pattern = new RegExp(`(${usable.join('|')})`, 'gi');
  return (
    <>
      {text.split(pattern).map((piece, index) =>
        pattern.test(piece) && index % 2 === 1 ? <mark key={index}>{piece}</mark> : <span key={index}>{piece}</span>,
      )}
    </>
  );
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
