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
  edge_type?: EdgeType | null;
  // No `reviewer_id`. It used to be a free-text field, which meant a reviewer could
  // attribute a decision to anyone — and §4.2's promise that human-confirmed facts stay
  // distinguishable from AI-generated ones is worth nothing if the human is unverified.
  // The server now takes it from the authenticated principal and rejects it in the body.
  // It stays on `EscalationItem` below, which is the read shape.
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

// --- Risk flags and redlines. Backed by backend/app/risk/routes.py and
// backend/app/redline/routes.py. These were served by a `dashboard_stubs.py` from
// hand-written fixtures once; that module is gone and both are real reads now.

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

// --- Documents, lineage and processing ----------------------------------------------
// Hand-mirrored from backend/app/schemas.py, like everything above. These are string
// unions, so a backend rename produces no TypeScript error — just an unstyled badge or a
// missing row. Each enum keeps an `as const` array so call sites can be exhaustive, and
// with noUncheckedIndexedAccess an indexed lookup is `T | undefined`, which turns silent
// drift into a visible "—" rather than a blank.

export const DOC_KINDS = ['contract', 'regulation'] as const;
export type DocKind = (typeof DOC_KINDS)[number];

export const PROCESSING_STATES = [
  'pending',
  'queued',
  'running',
  'succeeded',
  'failed',
] as const;
export type ProcessingState = (typeof PROCESSING_STATES)[number];

/** The pipeline stages, in order, for the upload progress rail. */
export const PROCESS_STAGES = ['ingestion', 'clause_ner', 'risk', 'redline'] as const;
export type ProcessStage = (typeof PROCESS_STAGES)[number];

export interface DocumentStatus {
  document_id: string;
  status: ProcessingState;
  stage: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  summary: Record<string, number>;
}

export interface DocumentSummary {
  document_id: string;
  filename: string;
  title: string | null;
  doc_kind: DocKind;
  jurisdiction: string | null;
  version: number;
  contract_family: string | null;
  parent_document_id: string | null;
  rbac_tags: string[];
  uploaded_at: string | null;
  processing_status: ProcessingState;
  risk_counts: Partial<Record<Severity, number>>;
  open_escalations: number;
}

export interface DocumentVersion {
  document_id: string;
  version: number;
  filename: string;
  title: string | null;
  uploaded_at: string | null;
  parent_document_id: string | null;
  processing_status: ProcessingState;
}

export interface UploadResponse {
  document_id: string;
  filename: string;
  status: string;
  pages: number;
  chunks: number;
  min_ocr_confidence: number;
  low_confidence_pages: number[];
  processing: { status: string; poll: string };
}

// --- Search --------------------------------------------------------------------------

export interface SearchHit {
  chunk_id: string;
  document_id: string;
  filename: string;
  doc_kind: DocKind;
  clause_ref: string;
  section_type: string;
  page: number;
  text: string;
  /** Plain text. The client highlights using SearchResponse.terms — no markup arrives. */
  snippet: string;
  score: number;
  jurisdiction: string | null;
  backend: string;
}

export interface SearchResponse {
  query: string;
  terms: string[];
  total: number;
  hits: SearchHit[];
  backend: string;
}

// --- Regulations ---------------------------------------------------------------------

export const MATCH_REASONS = ['authored', 'lexical'] as const;
export type MatchReason = (typeof MATCH_REASONS)[number];

export interface RegulationMatch {
  provision_id: string;
  citation: string;
  title: string;
  instrument: string;
  jurisdiction: string;
  summary: string;
  snippet: string;
  source_url: string;
  score: number;
  /** `authored` is a citation someone wrote; `lexical` is word overlap. Never conflate. */
  match_reason: MatchReason;
  matched_on: string[];
  notes: string | null;
}

// --- Summary -------------------------------------------------------------------------

export const CITATION_KINDS = ['clause', 'fact', 'risk', 'redline', 'edge'] as const;
export type CitationKind = (typeof CITATION_KINDS)[number];

export interface Citation {
  kind: CitationKind;
  ref: string;
}

export interface Claim {
  text: string;
  /** Never empty — the backend drops claims whose citations do not resolve. */
  sources: Citation[];
}

export interface RiskHighlight {
  risk_id: string;
  clause_ref: string;
  severity: Severity;
  rule_id: string;
  rule_version: number;
  statement: Claim;
}

export interface ContractSummary {
  document_id: string;
  generated_at: string;
  provider: 'mock' | 'claude';
  input_fingerprint: string;
  parties: Claim[];
  term: Claim | null;
  key_obligations: Claim[];
  payment: Claim | null;
  liability_cap: Claim | null;
  termination: Claim | null;
  governing_law: Claim | null;
  top_risks: RiskHighlight[];
  unusual_clauses: Claim[];
  /** What the summary could not see. Computed, not generated. */
  coverage_notes: string[];
}

export interface ContractSummaryResponse {
  summary: ContractSummary;
  cached: boolean;
  stale: boolean;
}

// --- Comparison ----------------------------------------------------------------------

export const CLAUSE_STATUSES = [
  'identical',
  'modified',
  'added',
  'removed',
  'renumbered',
] as const;
export type ClauseStatus = (typeof CLAUSE_STATUSES)[number];

export interface DiffSpan {
  op: 'equal' | 'insert' | 'delete' | 'replace';
  left: string;
  right: string;
}

export interface DocumentRef {
  document_id: string;
  filename: string;
  title: string | null;
  version: number;
  jurisdiction: string | null;
  uploaded_at: string | null;
}

export interface ClauseAlignment {
  left_ref: string | null;
  right_ref: string | null;
  heading: string;
  status: ClauseStatus;
  alignment_score: number;
  similarity: number;
  diff: DiffSpan[] | null;
}

export const RISK_DELTA_CHANGES = [
  'appeared',
  'resolved',
  'severity_changed',
  'suppression_changed',
  'unchanged',
] as const;
export type RiskDeltaChange = (typeof RISK_DELTA_CHANGES)[number];

export interface RiskDelta {
  change: RiskDeltaChange;
  rule_id: string;
  left_risk_id: string | null;
  right_risk_id: string | null;
  left_ref: string | null;
  right_ref: string | null;
  left_severity: Severity | null;
  right_severity: Severity | null;
  left_status: 'flagged' | 'suppressed' | null;
  right_status: 'flagged' | 'suppressed' | null;
  note: string;
}

export interface RedlineOutcome {
  redline_id: string;
  clause_ref: string;
  outcome: 'applied' | 'partially_applied' | 'not_applied' | 'clause_removed';
  similarity: number;
  right_ref: string | null;
}

export interface FactDelta {
  fact_type: string;
  left_ref: string | null;
  right_ref: string | null;
  left_value: Record<string, unknown> | null;
  right_value: Record<string, unknown> | null;
  change: 'added' | 'removed' | 'changed' | 'unchanged';
}

export interface ComparisonTotals {
  clauses_identical: number;
  clauses_modified: number;
  clauses_added: number;
  clauses_removed: number;
  risks_appeared: number;
  risks_resolved: number;
  risks_severity_changed: number;
}

export interface ComparisonResult {
  left: DocumentRef;
  right: DocumentRef;
  pairing: 'version' | 'cross_document';
  clauses: ClauseAlignment[];
  risk_deltas: RiskDelta[];
  redline_outcomes: RedlineOutcome[];
  fact_deltas: FactDelta[];
  totals: ComparisonTotals;
  caveats: string[];
}

/** Same RiskDelta shape as a comparison — one component renders both. */
export interface RiskPreview {
  document_id: string;
  base_jurisdiction: string | null;
  target_jurisdiction: string;
  delta: RiskDelta[];
  /** Rules with no counterpart in the target playbook. Rendering this is not optional. */
  unmapped_rules: string[];
  evaluated_rules: number;
}

// --- Negotiation ---------------------------------------------------------------------

export const NEGOTIATION_TIERS = ['preferred', 'acceptable', 'walk_away'] as const;
export type NegotiationTier = (typeof NEGOTIATION_TIERS)[number];

export interface NegotiationPosition {
  position_id: string;
  document_id: string;
  redline_id: string;
  risk_id: string;
  clause_ref: string;
  tier: NegotiationTier;
  rank: number;
  suggested_text: string;
  rationale: string;
  concession: string;
  residual_severity: Severity;
  grounded_in_override: string | null;
  confidence: number;
  created_at: string | null;
}

export interface NegotiationLadder {
  redline_id: string;
  clause_ref: string;
  risk_id: string;
  rule_id: string;
  rule_version: number;
  severity: Severity;
  original_text: string;
  positions: NegotiationPosition[];
  /** The playbook permits no concession here. Materially different from "we found none". */
  no_fallback_available: boolean;
}
