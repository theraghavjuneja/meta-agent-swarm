import { useOutletContext } from 'react-router-dom';
import { useUsage } from '../hooks/useCampaigns';
import EmptyState from '../components/EmptyState';
import { SkeletonLine } from '../components/Skeleton';
import { formatCurrency } from '../lib/status';

function SummaryCard({ label, value }) {
  return (
    <div className="border border-line rounded-md p-4">
      <p className="text-xs text-slate">{label}</p>
      <p className="font-display text-xl font-semibold text-ink mt-1">{value}</p>
    </div>
  );
}

export default function CampaignUsage() {
  const { campaign } = useOutletContext();
  const { data, isLoading, isError, refetch } = useUsage(campaign.id);

  if (isLoading) {
    return (
      <div className="space-y-3">
        <SkeletonLine width="w-full" />
        <SkeletonLine width="w-5/6" />
      </div>
    );
  }

  if (isError) {
    return (
      <EmptyState
        heading="Couldn't load usage"
        description="Something went wrong fetching cost and usage data for this campaign."
        action={
          <button
            type="button"
            onClick={() => refetch()}
            className="px-3 py-1.5 text-sm font-medium rounded-sm border border-line hover:border-ink"
          >
            Retry
          </button>
        }
      />
    );
  }

  return (
    <div>
      <div className="grid grid-cols-3 gap-4 mb-8 max-w-2xl">
        <SummaryCard label="Total cost" value={formatCurrency(data.total_cost_usd)} />
        <SummaryCard label="Estimated cost" value={formatCurrency(data.estimated_cost_usd)} />
        <SummaryCard label="Metered cost" value={formatCurrency(data.metered_cost_usd)} />
      </div>

      {data.includes_estimates ? (
        <p className="text-xs text-status-amber mb-4">
          These figures include estimated costs for operations not yet fully metered.
        </p>
      ) : null}

      {data.rows?.length ? (
        <table className="w-full text-left text-sm max-w-3xl">
          <thead>
            <tr className="border-b border-line text-slate">
              <th className="py-2.5 font-medium">Provider</th>
              <th className="py-2.5 font-medium">Operation</th>
              <th className="py-2.5 font-medium text-right">Cost</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, index) => (
              <tr key={index} className="border-b border-line">
                <td className="py-2.5 text-ink">{row.provider}</td>
                <td className="py-2.5 text-ink">{row.operation}</td>
                <td className="py-2.5 text-ink text-right">{formatCurrency(row.cost_usd ?? row.cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="text-sm text-slate">No usage recorded yet.</p>
      )}
    </div>
  );
}
