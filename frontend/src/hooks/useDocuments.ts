import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  compareDocuments,
  getDocumentStatus,
  getRiskPreview,
  getSummary,
  listDocuments,
  listNegotiationLadders,
  listVersions,
  processDocument,
  uploadDocument,
  type DocumentFilters,
  type UploadPayload,
} from '../api/client';

/**
 * Documents, processing and the features built on them.
 *
 * A separate file from `useReviewQueue.ts` rather than an addition to it: three other
 * features import that module, and growing it into a single 400-line hook barrel makes
 * every one of them churn on unrelated edits.
 */

export const documentsKey = (filters: DocumentFilters) => ['documents', filters] as const;

export function useDocuments(filters: DocumentFilters = {}) {
  return useQuery({
    queryKey: documentsKey(filters),
    queryFn: () => listDocuments(filters),
  });
}

export function useVersions(documentId: string | null) {
  return useQuery({
    queryKey: ['documents', 'versions', documentId] as const,
    queryFn: () => listVersions(documentId as string),
    enabled: documentId !== null,
  });
}

/**
 * Polls until the pipeline finishes.
 *
 * `refetchInterval` receives the Query object in react-query v5, not the data — the v4
 * signature would silently read `undefined` here and poll forever.
 */
export function useDocumentStatus(documentId: string | null) {
  return useQuery({
    queryKey: ['document-status', documentId] as const,
    queryFn: () => getDocumentStatus(documentId as string),
    enabled: documentId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'succeeded' || status === 'failed' ? false : 1500;
    },
  });
}

export function useUploadDocument() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: UploadPayload) => uploadDocument(payload),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['documents'] });
    },
  });
}

export function useProcessDocument() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ documentId, force }: { documentId: string; force?: boolean }) =>
      processDocument(documentId, force ?? false),
    onSuccess: (_data, variables) => {
      void client.invalidateQueries({ queryKey: ['document-status', variables.documentId] });
    },
  });
}

/**
 * Everything a finished pipeline run makes stale. Called when polling reports success —
 * without it the dashboard keeps showing the pre-run empty state until a manual reload.
 */
export function useInvalidateDocumentData() {
  const client = useQueryClient();
  return (documentId: string) => {
    for (const key of [
      ['risk-flags', documentId],
      ['redlines', documentId],
      ['kg-edges', documentId],
      ['facts', documentId],
      ['summary', documentId],
      ['negotiation', documentId],
      ['review-queue'],
      ['documents'],
    ]) {
      void client.invalidateQueries({ queryKey: key });
    }
  };
}

export function useSummary(documentId: string | null) {
  return useQuery({
    queryKey: ['summary', documentId] as const,
    queryFn: () => getSummary(documentId as string),
    enabled: documentId !== null,
  });
}

export function useComparison(left: string | null, right: string | null) {
  return useQuery({
    queryKey: ['comparison', left, right] as const,
    queryFn: () => compareDocuments(left as string, right as string),
    enabled: left !== null && right !== null && left !== right,
  });
}

export function useRiskPreview(documentId: string | null, jurisdiction: string | null) {
  return useQuery({
    queryKey: ['risk-preview', documentId, jurisdiction] as const,
    queryFn: () => getRiskPreview(documentId as string, jurisdiction as string),
    enabled: documentId !== null && jurisdiction !== null,
  });
}

export function useNegotiationLadders(documentId: string | null) {
  return useQuery({
    queryKey: ['negotiation', documentId] as const,
    queryFn: () => listNegotiationLadders(documentId as string),
    enabled: documentId !== null,
  });
}
