import { getAccessToken, onUnauthorized } from '../auth/session';
import type {
  ComparisonResult,
  ContractSummaryResponse,
  DocKind,
  DocumentStatus,
  DocumentSummary,
  DocumentVersion,
  EscalationItem,
  EscalationSource,
  Fact,
  KGEdge,
  NegotiationLadder,
  Redline,
  RegulationMatch,
  ResolveRequest,
  ReviewStatus,
  RiskFlag,
  RiskPreview,
  SearchResponse,
  UploadResponse,
} from './types';

/**
 * `/api` in development, where vite proxies to localhost:8000 and there is no CORS
 * preflight. A production build has no proxy, so `VITE_API_BASE` has to point at the
 * API's real origin — and that origin must appear in the backend's CORS `allow_origins`,
 * which currently lists only port 5173.
 */
const BASE = import.meta.env.VITE_API_BASE ?? '/api';

/** Carries the status code so callers can tell a 409 (someone else resolved it) apart. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    // Drop a dead credential once, here, rather than letting every later request retry
    // with it.
    if (response.status === 401) onUnauthorized();
    throw new ApiError(response.status, await detail(response));
  }
  return (await response.json()) as T;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    // `init?.headers` stays last so a caller can still override anything above it.
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...init?.headers },
  });
  return handle<T>(response);
}

/**
 * Multipart POST. Deliberately does not set `Content-Type`: the browser has to generate
 * it so it can include the multipart boundary, and setting it by hand produces a body
 * the server cannot parse.
 */
async function requestForm<T>(path: string, form: FormData): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method: 'POST',
    body: form,
    headers: authHeaders(),
  });
  return handle<T>(response);
}

async function detail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
    if (body.detail) return JSON.stringify(body.detail);
  } catch {
    // Non-JSON error body; fall through to the status text.
  }
  return `${response.status} ${response.statusText}`;
}

export interface QueueFilters {
  statuses?: ReviewStatus[];
  documentId?: string;
  source?: EscalationSource;
}

export function listReviewQueue(filters: QueueFilters = {}): Promise<EscalationItem[]> {
  const params = new URLSearchParams();
  for (const status of filters.statuses ?? []) params.append('status', status);
  if (filters.documentId) params.set('document_id', filters.documentId);
  if (filters.source) params.set('source', filters.source);

  const query = params.toString();
  return request<EscalationItem[]>(`/review-queue${query ? `?${query}` : ''}`);
}

export function getEscalation(id: string): Promise<EscalationItem> {
  return request<EscalationItem>(`/review-queue/${encodeURIComponent(id)}`);
}

export function resolveEscalation(
  id: string,
  body: ResolveRequest,
): Promise<EscalationItem> {
  return request<EscalationItem>(`/review-queue/${encodeURIComponent(id)}/resolve`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function listEdges(
  documentId: string,
  status: ReviewStatus = 'confirmed',
): Promise<KGEdge[]> {
  return request<KGEdge[]>(
    `/documents/${encodeURIComponent(documentId)}/kg/edges?status=${status}`,
  );
}

export function listFacts(documentId: string): Promise<Fact[]> {
  return request<Fact[]>(`/documents/${encodeURIComponent(documentId)}/facts`);
}

export function listRiskFlags(documentId: string): Promise<RiskFlag[]> {
  return request<RiskFlag[]>(`/documents/${encodeURIComponent(documentId)}/risk-flags`);
}

export function listRedlines(documentId: string): Promise<Redline[]> {
  return request<Redline[]>(`/documents/${encodeURIComponent(documentId)}/redlines`);
}

/**
 * No status filter, unlike `listRedlines`. This is what an escalation's
 * `target_redline_id` points at, and a held redline is exactly the one a reviewer
 * needs to see.
 */
export function getRedline(redlineId: string): Promise<Redline> {
  return request<Redline>(`/redlines/${encodeURIComponent(redlineId)}`);
}

// --- Documents, lineage, processing -------------------------------------------------

export interface DocumentFilters {
  docKind?: DocKind;
  jurisdiction?: string;
  contractFamily?: string;
}

export function listDocuments(filters: DocumentFilters = {}): Promise<DocumentSummary[]> {
  const params = new URLSearchParams();
  if (filters.docKind) params.set('doc_kind', filters.docKind);
  if (filters.jurisdiction) params.set('jurisdiction', filters.jurisdiction);
  if (filters.contractFamily) params.set('contract_family', filters.contractFamily);
  const query = params.toString();
  return request<DocumentSummary[]>(`/documents${query ? `?${query}` : ''}`);
}

export function listVersions(documentId: string): Promise<DocumentVersion[]> {
  return request<DocumentVersion[]>(`/documents/${encodeURIComponent(documentId)}/versions`);
}

export function getDocumentStatus(documentId: string): Promise<DocumentStatus> {
  return request<DocumentStatus>(`/documents/${encodeURIComponent(documentId)}/status`);
}

export function processDocument(documentId: string, force = false): Promise<DocumentStatus> {
  return request<DocumentStatus>(
    `/documents/${encodeURIComponent(documentId)}/process${force ? '?force=true' : ''}`,
    { method: 'POST' },
  );
}

export interface UploadPayload {
  file: File;
  rbacTags: string[];
  docKind: DocKind;
  jurisdiction?: string;
  title?: string;
  parentDocumentId?: string;
}

export function uploadDocument(payload: UploadPayload): Promise<UploadResponse> {
  const form = new FormData();
  form.set('file', payload.file);
  form.set('rbac_tags', payload.rbacTags.join(','));
  form.set('doc_kind', payload.docKind);
  if (payload.jurisdiction) form.set('jurisdiction', payload.jurisdiction);
  if (payload.title) form.set('title', payload.title);
  if (payload.parentDocumentId) form.set('parent_document_id', payload.parentDocumentId);
  return requestForm<UploadResponse>('/ingest/upload', form);
}

// --- Search, regulations, summary ---------------------------------------------------

export interface SearchParams {
  q: string;
  docKind?: DocKind;
  documentIds?: string[];
  jurisdiction?: string;
  limit?: number;
}

export function search(params: SearchParams): Promise<SearchResponse> {
  const query = new URLSearchParams({ q: params.q });
  if (params.docKind) query.set('doc_kind', params.docKind);
  for (const id of params.documentIds ?? []) query.append('document_id', id);
  if (params.jurisdiction) query.set('jurisdiction', params.jurisdiction);
  if (params.limit) query.set('limit', String(params.limit));
  return request<SearchResponse>(`/search?${query.toString()}`);
}

export function listRegulationsForRisk(riskId: string): Promise<RegulationMatch[]> {
  return request<RegulationMatch[]>(`/risk-flags/${encodeURIComponent(riskId)}/regulations`);
}

export function getSummary(
  documentId: string,
  refresh = false,
): Promise<ContractSummaryResponse> {
  return request<ContractSummaryResponse>(
    `/documents/${encodeURIComponent(documentId)}/summary${refresh ? '?refresh=true' : ''}`,
  );
}

// --- Compare, jurisdiction preview, negotiation -------------------------------------

export function compareDocuments(
  left: string,
  right: string,
  diffDetail: 'none' | 'changed_only' | 'all' = 'changed_only',
): Promise<ComparisonResult> {
  const params = new URLSearchParams({ left, right, diff_detail: diffDetail });
  return request<ComparisonResult>(`/compare?${params.toString()}`);
}

export function getRiskPreview(documentId: string, jurisdiction: string): Promise<RiskPreview> {
  const params = new URLSearchParams({ jurisdiction });
  return request<RiskPreview>(
    `/documents/${encodeURIComponent(documentId)}/risk-preview?${params.toString()}`,
  );
}

export function listNegotiationLadders(documentId: string): Promise<NegotiationLadder[]> {
  return request<NegotiationLadder[]>(
    `/documents/${encodeURIComponent(documentId)}/negotiation`,
  );
}

export function generateNegotiation(documentId: string): Promise<{ ladders: number; positions: number }> {
  return request<{ ladders: number; positions: number }>(
    `/documents/${encodeURIComponent(documentId)}/negotiation/generate`,
    { method: 'POST' },
  );
}
