/**
 * TypeScript mirrors of `backend/app/schemas.py`.
 *
 * The status values below are the shared enum from WORK-SPLIT.md — exactly three,
 * lowercase, no variants. Writing `'pending'` here instead of `'pending_review'` would
 * not error anywhere; the queue would just quietly show fewer items than exist. Keep
 * this file in lockstep with `schemas.py` and change both in the same PR.
 */

export const REVIEW_STATUSES = ['pending_review', 'confirmed', 'rejected'] as const;
export type ReviewStatus = (typeof REVIEW_STATUSES)[number];

export type EscalationSource = 'clause_ner' | 'redline_generator';

export type EscalationReason = 'ambiguous_edge' | 'budget_exhausted' | 'low_confidence';

export const EDGE_TYPES = ['OVERRIDES', 'DEPENDS_ON', 'WAIVES', 'MODIFIES'] as const;
export type EdgeType = (typeof EDGE_TYPES)[number];

export type Provenance =
  | 'direct_extraction'
  | 'retry:kg_precedent'
  | 'retry:widen_context'
  | 'retry:alternate_parse'
  | 'human';

/**
 * One round of an agent loop. Deliberately identical for both escalation sources:
 * Clause NER puts a retry strategy in `attempt`, the Redline Generator puts the query
 * it issued. One component renders both.
 */
export interface TraceRound {
  round: number;
  attempt: string;
  result: string;
  resolved: boolean;
}

/**
 * Both targets are nullable and they are mutually exclusive in practice: Clause NER
 * escalates a candidate edge, the Redline Generator escalates a held redline, and a
 * `budget_exhausted` escalation legitimately writes neither. Dropping either one from
 * this interface silently strands half the queue on a detail view that links nowhere.
 */
export interface EscalationItem {
  id: string;
  target_edge_id: string | null;
  target_redline_id: string | null;
  status: ReviewStatus;
  source: EscalationSource;
  reason: EscalationReason;
  document_id: string;
  clause_ref: string;
  rounds_attempted: number;
  trace: TraceRound[];
  reviewer_id: string | null;
  resolved_at: string | null;
}

export interface ResolveRequest {
  status: Extract<ReviewStatus, 'confirmed' | 'rejected'>;
  reviewer_id: string;
  edge_type?: EdgeType | null;
}

export interface KGEdge {
  edge_id: string;
  document_id: string;
  src_clause_ref: string;
  dst_clause_ref: string;
  edge_type: EdgeType;
  status: ReviewStatus;
  confidence: number;
  evidence_chunk_ids: string[];
  pattern_key: string | null;
  resolved_by: Provenance | null;
}

export interface Fact {
  fact_id: string;
  document_id: string;
  clause_ref: string;
  fact_type: string;
  value: Record<string, unknown>;
  confidence: number;
  source_chunk_ids: string[];
  provenance: Provenance;
}

// --- Stub shapes: Dev 4 and the Redline Generator own these; see dashboard_stubs.py ---

export type Severity = 'low' | 'medium' | 'high' | 'critical';

/**
 * Mirrors `RiskFlag` in schemas.py, which now follows Dev 4's own `schema.sql`.
 * `suppressed` means a confirmed KG edge waives the violation, so the Risk Engine
 * already decided it is not a finding — the dashboard still shows it, struck through,
 * because "we looked and dismissed it" is not the same as "we never looked".
 */
export interface RiskFlag {
  id: string;
  document_id: string;
  clause_ref: string;
  rule_id: string;
  rule_version: number;
  severity: Severity;
  status: 'flagged' | 'suppressed';
  suppressing_edge_id: string | null;
  triggering_fact_ids: string[];
}

export interface Redline {
  redline_id: string;
  document_id: string;
  risk_id: string;
  clause_ref: string;
  status: ReviewStatus;
  confidence: number;
  original_text: string;
  suggested_text: string;
  rationale: string;
  rounds_attempted: number;
  trace: TraceRound[];
}
