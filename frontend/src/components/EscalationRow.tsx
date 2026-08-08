import { Link } from 'react-router-dom';

import type { EscalationItem } from '../api/types';
import { ReasonBadge, StatusBadge } from './Badges';
import { ChevronRightIcon } from './Icons';
import { AttemptSummary, TriggerText } from './TraceTimeline';

/**
 * One queue row. The reviewer decides whether to open this from the row alone, so the
 * row carries the things that drive that decision: what actually flagged it, and
 * whether the agent already burned rounds trying to resolve it itself.
 */
export function EscalationRow({ item }: { item: EscalationItem }) {
  return (
    <tr>
      <th scope="row">
        <Link className="row-id" to={`/queue/${encodeURIComponent(item.id)}`}>
          {item.id}
          <ChevronRightIcon size={13} />
        </Link>
      </th>
      <td className="mono">{item.document_id}</td>
      <td>
        <code>{item.clause_ref}</code>
      </td>
      <td>
        <TriggerText trace={item.trace} reason={item.reason} />
      </td>
      <td>
        <ReasonBadge reason={item.reason} />
      </td>
      <td>
        <AttemptSummary rounds={item.rounds_attempted} />
      </td>
      <td>
        <StatusBadge status={item.status} />
        {item.reviewer_id ? <span className="reviewer"> by {item.reviewer_id}</span> : null}
      </td>
    </tr>
  );
}
