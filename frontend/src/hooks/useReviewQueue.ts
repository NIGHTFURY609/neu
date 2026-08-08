import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getEscalation,
  getRedline,
  listEdges,
  listFacts,
  listRedlines,
  listReviewQueue,
  listRiskFlags,
  resolveEscalation,
  type QueueFilters,
} from '../api/client';
import type { EscalationItem, ResolveRequest } from '../api/types';

export const queueKey = (filters: QueueFilters) => ['review-queue', filters] as const;

export function useReviewQueue(filters: QueueFilters) {
  return useQuery({
    queryKey: queueKey(filters),
    queryFn: () => listReviewQueue(filters),
  });
}

export function useEscalation(id: string) {
  return useQuery({
    queryKey: ['review-queue', 'item', id],
    queryFn: () => getEscalation(id),
  });
}

/**
 * Resolving touches two stores: the escalation row and the candidate KG edge. Both
 * caches are invalidated, otherwise the dashboard keeps showing a confirmed-edge count
 * that no longer matches what the Risk Engine would read.
 */
export function useResolveEscalation() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ResolveRequest }) =>
      resolveEscalation(id, body),
    onSuccess: (item: EscalationItem) => {
      void client.invalidateQueries({ queryKey: ['review-queue'] });
      void client.invalidateQueries({ queryKey: ['kg-edges', item.document_id] });
    },
  });
}

export function useRiskFlags(documentId: string) {
  return useQuery({
    queryKey: ['risk-flags', documentId],
    queryFn: () => listRiskFlags(documentId),
  });
}

export function useRedlines(documentId: string) {
  return useQuery({
    queryKey: ['redlines', documentId],
    queryFn: () => listRedlines(documentId),
  });
}

/**
 * One redline by id, for an escalation that targets one. Disabled when there is no
 * target — an escalation with neither target is a legitimate `budget_exhausted` row,
 * not a missing fetch.
 */
export function useRedline(redlineId: string | null) {
  return useQuery({
    queryKey: ['redline', redlineId],
    queryFn: () => getRedline(redlineId as string),
    enabled: redlineId !== null,
  });
}

export function useConfirmedEdges(documentId: string) {
  return useQuery({
    queryKey: ['kg-edges', documentId],
    queryFn: () => listEdges(documentId, 'confirmed'),
  });
}

export function useFacts(documentId: string) {
  return useQuery({
    queryKey: ['facts', documentId],
    queryFn: () => listFacts(documentId),
  });
}
