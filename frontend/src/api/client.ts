import type {
  EscalationItem,
  EscalationSource,
  Fact,
  KGEdge,
  Redline,
  ResolveRequest,
  ReviewStatus,
  RiskFlag,
} from './types';

// Same-origin `/api` is proxied to FastAPI during local development. Deployments can
// point this to their API gateway without rebuilding the client.
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });

  if (!response.ok) {
    throw new ApiError(response.status, await detail(response));
  }
  return (await response.json()) as T;
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
