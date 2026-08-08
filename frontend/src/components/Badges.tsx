import type { EscalationReason, EscalationSource, ReviewStatus, Severity } from '../api/types';
import { CheckIcon, CircleIcon, XIcon } from './Icons';

/**
 * There is no color in this UI, so a status is told apart by three things at
 * once: its glyph, its border treatment (dashed = still open, solid = decided)
 * and its fill (solid white = confirmed, hatched = rejected). See `styles.css`.
 * The label is always present, so none of that is load-bearing on its own.
 */

const STATUS_LABEL: Record<ReviewStatus, string> = {
  pending_review: 'Pending review',
  confirmed: 'Confirmed',
  rejected: 'Rejected',
};

const STATUS_ICON: Record<ReviewStatus, typeof CheckIcon> = {
  pending_review: CircleIcon,
  confirmed: CheckIcon,
  rejected: XIcon,
};

export function StatusBadge({ status }: { status: ReviewStatus }) {
  const Icon = STATUS_ICON[status];
  return (
    <span className={`badge badge-${status}`}>
      <Icon size={12} />
      {STATUS_LABEL[status]}
    </span>
  );
}

const SOURCE_LABEL: Record<EscalationSource, string> = {
  clause_ner: 'Clause NER',
  redline_generator: 'Redline Generator',
};

export function SourceBadge({ source }: { source: EscalationSource }) {
  return <span className={`badge badge-source-${source}`}>{SOURCE_LABEL[source]}</span>;
}

const REASON_LABEL: Record<EscalationReason, string> = {
  ambiguous_edge: 'Ambiguous relationship',
  budget_exhausted: 'Retrieval budget exhausted',
  low_confidence: 'Confidence below threshold',
};

export function ReasonBadge({ reason }: { reason: EscalationReason }) {
  return <span className="badge badge-reason">{REASON_LABEL[reason]}</span>;
}

const SEVERITY_STEPS: Record<Severity, number> = {
  low: 1,
  medium: 2,
  high: 3,
  critical: 4,
};

/** Four bars, N of them lit — severity stays readable when every badge is grey. */
export function SeverityBadge({ severity }: { severity: Severity }) {
  const lit = SEVERITY_STEPS[severity];
  return (
    <span className={`badge badge-severity-${severity}`}>
      <span className="sev-bars" aria-hidden="true">
        {[1, 2, 3, 4].map((step) => (
          <span key={step} className={step <= lit ? 'sev-bar on' : 'sev-bar'} />
        ))}
      </span>
      {severity}
    </span>
  );
}

export function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <span
      className="confidence"
      role="meter"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="Confidence"
    >
      <span className="confidence-track">
        <span className="confidence-fill" style={{ width: `${pct}%` }} />
      </span>
      <span className="confidence-value">{pct}%</span>
    </span>
  );
}

/**
 * A jurisdiction code: US-NY, EU, IN, GENERAL.
 *
 * An unknown jurisdiction reuses `.badge-pending_review` — the dashed, hollow treatment
 * that already means "not decided" everywhere else in this system. A document whose
 * jurisdiction was never set is undecided, not absent, and the rule set applied to it
 * depends on the answer.
 */
export function JurisdictionBadge({ code }: { code: string | null }) {
  if (!code) {
    return (
      <span className="badge badge-pending_review badge-jurisdiction" title="No jurisdiction set">
        unset
      </span>
    );
  }
  return <span className="badge badge-jurisdiction">{code.toUpperCase()}</span>;
}
