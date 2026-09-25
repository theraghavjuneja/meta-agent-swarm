export default function AngleCard({ angle, onSelect, isSelecting, anySelected }) {
  return (
    <div
      className={`rounded-md border p-4 ${
        angle.is_selected ? 'border-signal bg-signal/[0.04]' : 'border-line'
      }`}
    >
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-mono text-slate">Angle {angle.angle_number}</span>
        {angle.is_selected ? (
          <span className="text-xs font-medium text-signal">Selected</span>
        ) : null}
      </div>
      <h3 className="mt-1.5 font-display text-base font-semibold text-ink">{angle.hook}</h3>
      {angle.observations?.length ? (
        <div className="mt-3">
          <p className="text-xs font-medium uppercase tracking-wide text-slate">
            Sourced observations
          </p>
          <ul className="mt-1.5 space-y-2 text-sm">
            {angle.observations.map((obs) => (
              <li key={obs.finding_id} className="border-l-2 border-line pl-2">
                <p className="text-ink">{obs.observation}</p>
                <p className="mt-0.5 text-slate italic">“{obs.quote}”</p>
                <a
                  href={obs.source_url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="text-xs text-signal hover:underline break-all"
                >
                  {obs.finding_id} · {obs.lens} · {obs.source_title}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <p className="mt-4 text-xs font-medium uppercase tracking-wide text-slate">
        Creative interpretation
      </p>
      <dl className="mt-1.5 space-y-2 text-sm">
        <div>
          <dt className="text-slate">Audience insight</dt>
          <dd className="text-ink mt-0.5">{angle.audience_insight}</dd>
        </div>
        <div>
          <dt className="text-slate">Visual direction</dt>
          <dd className="text-ink mt-0.5">{angle.visual_direction}</dd>
        </div>
        <div>
          <dt className="text-slate">Rationale</dt>
          <dd className="text-ink mt-0.5">{angle.rationale}</dd>
        </div>
      </dl>
      {!angle.is_selected && !anySelected ? (
        <button
          type="button"
          disabled={isSelecting}
          onClick={() => onSelect(angle.id)}
          className="mt-4 px-3 py-1.5 text-sm font-medium rounded-sm bg-ink text-white hover:bg-ink/90 disabled:opacity-50"
        >
          {isSelecting ? 'Selecting…' : 'Select this angle'}
        </button>
      ) : null}
    </div>
  );
}
