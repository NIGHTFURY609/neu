import { useQuery } from '@tanstack/react-query';

import { listRegulationsForRisk, search, type SearchParams } from '../api/client';

export function useSearch(params: SearchParams) {
  const trimmed = params.q.trim();
  return useQuery({
    queryKey: ['search', { ...params, q: trimmed }] as const,
    queryFn: () => search({ ...params, q: trimmed }),
    // The route itself requires two characters; matching that here avoids a guaranteed
    // 422 on the first keystroke.
    enabled: trimmed.length >= 2,
  });
}

export function useRegulationsForRisk(riskId: string | null) {
  return useQuery({
    queryKey: ['regulations', 'risk', riskId] as const,
    queryFn: () => listRegulationsForRisk(riskId as string),
    enabled: riskId !== null,
  });
}
