import { useActionState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { ApiError } from '../api/client';
import { EDGE_TYPES, type EdgeType, type EscalationItem, type ReviewStatus } from '../api/types';
import { ReasonBadge, SourceBadge, StatusBadge } from '../components/Badges';
import { ArrowLeftIcon, CheckIcon, XIcon } from '../components/Icons';
import { AttemptSummary, TraceTimeline } from '../components/TraceTimeline';
import { useEscalation, useRedline, useResolveEscalation } from '../hooks/useReviewQueue';

export function EscalationDetail() {
  const { id = '' } = useParams();
  const { data, isPending, isError, error } = useEscalation(id);

  if (isPending) return <p className="muted">Loading escalation…</p>;
  if (isError) {
    return (
      <p className="panel panel-error" role="alert">
        {(error as Error).message}
      </p>
    );
  }

  return <EscalationView item={data} />;
}

function EscalationView({ item }: { item: EscalationItem }) {
  const resolve = useResolveEscalation();
  const isOpen = item.status === 'pending_review';

  return (
    <article>
      <p className="breadcrumb">
        <Link to="/queue">
          <ArrowLeftIcon size={14} />
          Back to queue
        </Link>
      </p>

      <header className="page-head">
        <h1 className="mono">{item.id}</h1>
        <div className="badge-row">
          <StatusBadge status={item.status} />
          <SourceBadge source={item.source} />
          <ReasonBadge reason={item.reason} />
        </div>
      </header>

      <dl className="facts">
        <dt>Document</dt>
        <dd>{item.document_id}</dd>
        <dt>Clause</dt>
        <dd>
          <code>{item.clause_ref}</code>
        </dd>
        <dt>Target</dt>
        <dd>
          <EscalationTarget item={item} />
        </dd>
        <dt>Attempted</dt>
        <dd>
          <AttemptSummary rounds={item.rounds_attempted} />
        </dd>
        {item.reviewer_id ? (
          <>
            <dt>Resolved by</dt>
            <dd>
              {item.reviewer_id}
              {item.resolved_at ? ` on ${new Date(item.resolved_at).toLocaleString()}` : ''}
            </dd>
          </>
        ) : null}
      </dl>

      <section className="panel">
        <h2>What the agent tried</h2>
        <TraceTimeline trace={item.trace} source={item.source} />
      </section>

      {/* Lives outside the form on purpose: a successful resolve replaces the form
          with the resolved state, and the acknowledgement has to outlive that. */}
      {resolve.isSuccess ? (
        <p className="panel ok" role="status" aria-live="polite">
          <CheckIcon size={15} />{' '}
          {`Recorded as ${resolve.data.status} by ${resolve.data.reviewer_id}.`}
        </p>
      ) : null}

      {isOpen ? (
        <ResolveForm item={item} resolve={resolve} />
      ) : (
        <p className="panel panel-empty">
          Already {item.status}. Resolutions are permanent — the decision and its
          reviewer stay in the audit trail.
        </p>
      )}
    </article>
  );
}

/**
 * Three branches, because there are three real cases. Clause NER escalates a candidate
 * edge; the Redline Generator escalates a held redline and points `target_redline_id` at
 * it; a `budget_exhausted` escalation deliberately writes no redline row at all, so
 * "neither" is a correct outcome rather than missing data — and saying so is the
 * difference between an answer and a dead dash.
 */
function EscalationTarget({ item }: { item: EscalationItem }) {
  const redline = useRedline(item.target_redline_id);

  if (item.target_edge_id) {
    return (
      <>
        Candidate edge <code>{item.target_edge_id}</code>
      </>
    );
  }

  if (item.target_redline_id) {
    return (
      <>
        Held redline <code>{item.target_redline_id}</code>
        {redline.isPending ? <span className="muted"> · loading…</span> : null}
        {redline.isError ? (
          <span className="muted"> · could not load ({(redline.error as Error).message})</span>
        ) : null}
        {redline.data ? (
          <p className="muted">
            Section {redline.data.clause_ref} · {redline.data.status} · confidence{' '}
            {redline.data.confidence} · {redline.data.rounds_attempted} round(s)
          </p>
        ) : null}
      </>
    );
  }

  return (
    <span className="muted">
      No target — the loop exhausted its budget without writing a redline.
    </span>
  );
}

/** Errors only — the success acknowledgement is rendered by `EscalationView`. */
type ResolveState = { message: string } | null;

function ResolveForm({
  item,
  resolve,
}: {
  item: EscalationItem;
  resolve: ReturnType<typeof useResolveEscalation>;
}) {
  // Only a Clause NER escalation has an edge whose type the reviewer can pin; the
  // Redline Generator's escalations have no KG edge behind them.
  const canPinEdgeType = item.source === 'clause_ner' && item.target_edge_id !== null;

  const submit = async (_prev: ResolveState, formData: FormData): Promise<ResolveState> => {
    const status = formData.get('status') as Extract<ReviewStatus, 'confirmed' | 'rejected'>;
    const reviewerId = String(formData.get('reviewer_id') ?? '').trim();
    const edgeType = (formData.get('edge_type') || null) as EdgeType | null;

    if (!reviewerId) {
      return { message: 'A reviewer id is required — resolutions are attributed.' };
    }

    try {
      await resolve.mutateAsync({
        id: item.id,
        body: { status, reviewer_id: reviewerId, edge_type: edgeType },
      });
      return null;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        return { message: `${err.message} Reload to see their decision.` };
      }
      return { message: (err as Error).message };
    }
  };

  const [state, action, isSubmitting] = useActionState<ResolveState, FormData>(submit, null);

  return (
    <form className="panel resolve" action={action}>
      <h2>Resolve</h2>
      <p className="muted">
        Confirming writes the decision back to the knowledge graph tagged{' '}
        <code>human</code>, so the Risk Engine can see the edge and nobody later mistakes
        it for an AI-generated one.
      </p>

      <label>
        <span className="field-label">Reviewer id</span>
        <input name="reviewer_id" required minLength={1} placeholder="your id" />
      </label>

      {canPinEdgeType ? (
        <label>
          <span className="field-label">Correct relationship</span>
          <select name="edge_type" defaultValue="">
            <option value="">Leave as extracted</option>
            {EDGE_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      <div className="actions">
        <button type="submit" name="status" value="confirmed" disabled={isSubmitting}>
          <CheckIcon size={15} />
          {isSubmitting ? 'Saving…' : 'Confirm'}
        </button>
        <button
          type="submit"
          name="status"
          value="rejected"
          className="secondary"
          disabled={isSubmitting}
        >
          <XIcon size={15} />
          Reject
        </button>
      </div>

      <p role="alert" className="error">
        {state?.message ?? ''}
      </p>
    </form>
  );
}
