import type { Severity } from '../../api/types';

const ORDER: Severity[] = ['critical', 'high', 'medium', 'low'];

/**
 * One stacked bar, proportional by count.
 *
 * No chart library, and no hue. Carbon conveys status through shade, border and texture
 * only, so the four segments reuse the *same* ink ramp `SeverityBadge` already uses —
 * which means this agrees with every severity badge on the page by construction rather
 * than by someone remembering to keep two palettes in sync.
 *
 * A pie would be unreadable in pure greyscale. A stacked bar plus a legend that always
 * prints the numbers is legible either way, which is the point: the chart is never the
 * only carrier of the information.
 */
export function SeverityRamp({
  counts,
  label,
}: {
  counts: Partial<Record<Severity, number>>;
  label: string;
}) {
  const values = ORDER.map((severity) => ({ severity, count: counts[severity] ?? 0 }));
  const total = values.reduce((sum, entry) => sum + entry.count, 0);

  if (total === 0) {
    return (
      <figure className="ramp-figure">
        <div className="panel-empty ramp-empty">No open findings.</div>
        <figcaption className="muted">{label}</figcaption>
      </figure>
    );
  }

  return (
    <figure className="ramp-figure">
      <div
        className="ramp"
        role="img"
        aria-label={`${label}: ${values.map((v) => `${v.count} ${v.severity}`).join(', ')}`}
      >
        {values
          .filter((entry) => entry.count > 0)
          .map((entry) => (
            <span
              key={entry.severity}
              className={`ramp-seg ramp-seg-${entry.severity}`}
              style={{ flexGrow: entry.count }}
              title={`${entry.count} ${entry.severity}`}
            />
          ))}
      </div>
      <figcaption className="ramp-legend mono">
        {values.map((entry) => (
          <span key={entry.severity}>
            {entry.severity} {entry.count}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
