import { useOutletContext } from 'react-router-dom';
import { useResearch, useSelectAngle } from '../hooks/useCampaigns';
import Timeline from '../components/Timeline';
import SourceList from '../components/SourceList';
import AngleCard from '../components/AngleCard';
import EmptyState from '../components/EmptyState';
import { SkeletonLine } from '../components/Skeleton';

export default function CampaignResearch() {
  const { campaign } = useOutletContext();
  const { data, isLoading, isError, refetch } = useResearch(campaign.id, campaign.status);
  const selectAngle = useSelectAngle(campaign.id);

  if (isLoading) {
    return (
      <div className="space-y-3">
        <SkeletonLine width="w-full" />
        <SkeletonLine width="w-5/6" />
        <SkeletonLine width="w-2/3" />
      </div>
    );
  }

  if (isError) {
    return (
      <EmptyState
        heading="Couldn't load research"
        description="Something went wrong fetching the research trace for this campaign."
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

  if (!data?.run) {
    return (
      <EmptyState
        heading="No research started yet"
        description="Research kicks off automatically once the campaign is created."
      />
    );
  }

  const anySelected = Boolean(data.selected_angle_id);

  return (
    <div className="space-y-10">
      {data.run.status === 'running' ? (
        <p className="text-sm text-status-blue">Research is running — this updates automatically.</p>
      ) : null}

      <section>
        <h2 className="font-display text-base font-semibold text-ink mb-3">Research trace</h2>
        <Timeline steps={data.steps} />
      </section>

      <section>
        <h2 className="font-display text-base font-semibold text-ink mb-3">
          Sources <span className="text-slate font-normal">({data.source_count ?? data.sources?.length ?? 0})</span>
        </h2>
        <SourceList sources={data.sources} />
      </section>

      <section>
        <h2 className="font-display text-base font-semibold text-ink mb-3">Proposed angles</h2>
        {data.angles?.length ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {data.angles.map((angle) => (
              <AngleCard
                key={angle.id}
                angle={angle}
                anySelected={anySelected}
                isSelecting={selectAngle.isPending && selectAngle.variables === angle.id}
                onSelect={(angleId) => selectAngle.mutate(angleId)}
              />
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate">
            Angles will appear here once research finishes running.
          </p>
        )}
      </section>
    </div>
  );
}
