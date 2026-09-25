import { formatDate } from '../lib/status';

function formatStepTitle(type) {
  if (!type) return 'Step';
  switch (type) {
    case 'search': return 'Search';
    case 'read_page': return 'Read Page';
    case 'decide': return 'Decide';
    default: return type.charAt(0).toUpperCase() + type.slice(1).replace(/_/g, ' ');
  }
}

export default function Timeline({ steps }) {
  if (!steps?.length) {
    return <p className="text-sm text-slate">No steps recorded yet.</p>;
  }

  return (
    <ol className="space-y-4">
      {steps.map((step, index) => {
        const phase = step.input?.phase;
        const title =
          step.title ||
          step.name ||
          (phase === 'plan'
            ? 'Plan research'
            : phase === 'synthesise'
              ? 'Synthesize angles'
              : phase === 'continue_check'
                ? 'Coverage check'
                : formatStepTitle(step.step_type));
        const lens = step.input?.lens || step.input?.arguments?.lens;
        const rejected = step.output?.error;
        const description = step.decision_summary || step.description;
        const timestamp = step.created_at || step.timestamp;
        
        return (
          <li key={step.id || step.step_number || index} className="flex gap-3">
            <div className="flex flex-col items-center pt-0.5">
              <span className="h-2 w-2 rounded-full bg-ink" />
              {index < steps.length - 1 ? <span className="w-px flex-1 bg-line mt-1" /> : null}
            </div>
            <div className="pb-4 w-full">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium text-ink">{title}</p>
                {timestamp ? (
                  <p className="text-xs text-slate mt-1 font-mono">{formatDate(timestamp)}</p>
                ) : null}
              </div>
              
              {lens ? <p className="mt-1 text-xs font-mono text-signal">lens: {lens}</p> : null}

              {phase === 'plan' && step.output?.plan?.questions ? (
                <ul className="mt-2 space-y-1 text-xs text-slate">
                  {step.output.plan.questions.map((q) => (
                    <li key={q.lens}>
                      <span className="font-mono text-ink">{q.lens}</span> — {q.question}
                    </li>
                  ))}
                </ul>
              ) : null}

              {step.step_type === 'search' && (step.input?.query || step.input?.arguments?.query) ? (
                <div className="mt-2 p-2 bg-surface border border-line rounded-sm text-xs font-mono text-slate break-all">
                  &gt; {step.input.query || step.input.arguments.query}
                </div>
              ) : null}

              {step.step_type === 'search' && step.output?.screened_out?.length ? (
                <details className="mt-1 text-xs text-slate">
                  <summary className="cursor-pointer">
                    {step.output.kept?.length ?? 0} kept, {step.output.screened_out.length} screened out
                  </summary>
                  <ul className="mt-1 space-y-0.5">
                    {step.output.screened_out.map((r) => (
                      <li key={r.url} className="break-all">
                        {r.url} — {r.reason}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}

              {step.step_type === 'read_page' && (step.input?.url || step.output?.title) ? (
                <div className="mt-2 text-sm text-ink truncate">
                  📄 {step.output?.title || step.input?.url}
                  {step.output?.source_type ? (
                    <span className="ml-2 text-xs text-slate">({step.output.source_type})</span>
                  ) : null}
                </div>
              ) : null}

              {step.step_type === 'read_page' && step.output?.relevant === false && !rejected ? (
                <p className="mt-1 text-xs text-status-amber">
                  Not counted as a source: {step.output.relevance_note || step.output.extraction_error}
                </p>
              ) : null}

              {step.output?.findings?.length ? (
                <ul className="mt-2 space-y-1 text-xs">
                  {step.output.findings.map((f) => (
                    <li key={f.id} className="text-slate">
                      <span className="font-mono text-ink">{f.id}</span> {f.observation}{' '}
                      <span className="italic">“{f.quote}”</span>
                    </li>
                  ))}
                </ul>
              ) : null}

              {rejected ? <p className="mt-1 text-xs text-status-red">Rejected: {rejected}</p> : null}
              
              {description ? (
                <p className="text-sm text-slate mt-2">{description}</p>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
