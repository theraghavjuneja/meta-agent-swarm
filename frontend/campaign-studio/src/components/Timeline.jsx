import { formatDate } from '../lib/status';

function formatStepTitle(type) {
  if (!type) return 'Step';
  switch (type) {
    case 'search': return 'Search';
    case 'read_page': return 'Read Page';
    case 'decide': return 'Synthesize & Decide';
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
        const title = step.title || step.name || formatStepTitle(step.step_type);
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
              
              {step.step_type === 'search' && step.input?.query ? (
                <div className="mt-2 p-2 bg-surface border border-line rounded-sm text-xs font-mono text-slate break-all">
                  &gt; {step.input.query}
                </div>
              ) : null}
              
              {step.step_type === 'read_page' && (step.input?.url || step.output?.title) ? (
                <div className="mt-2 text-sm text-ink truncate">
                  📄 {step.output?.title || step.input?.url}
                </div>
              ) : null}
              
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
