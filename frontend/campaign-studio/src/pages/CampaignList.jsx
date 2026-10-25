import { useNavigate, useSearchParams } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import StatusFilter from '../components/StatusFilter';
import CampaignTable from '../components/CampaignTable';
import Pagination from '../components/Pagination';
import EmptyState from '../components/EmptyState';
import { SkeletonTable } from '../components/Skeleton';
import { useCampaignList } from '../hooks/useCampaigns';

const LIMIT = 50;

export default function CampaignList() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const status = searchParams.get('status') || 'all';
  const offset = Number(searchParams.get('offset') || 0);

  const { data, isLoading, isError, refetch } = useCampaignList({
    status,
    limit: LIMIT,
    offset,
  });

  function updateParam(key, value) {
    const next = new URLSearchParams(searchParams);
    if (value === null || value === undefined || value === '') {
      next.delete(key);
    } else {
      next.set(key, value);
    }
    if (key === 'status') next.delete('offset');
    setSearchParams(next);
  }

  return (
    <div>
      <PageHeader
        title="Campaigns"
        description="Every product brief you've sent to Campaign Studio, and where it stands."
        actions={
          <button
            type="button"
            onClick={() => navigate('/campaigns/new')}
            className="px-4 py-2 text-sm font-medium rounded-sm bg-signal text-white hover:bg-signalDark"
          >
            Create campaign
          </button>
        }
      />

      <div className="flex items-center justify-between mb-4">
        <StatusFilter value={status} onChange={(value) => updateParam('status', value)} />
      </div>

      {isLoading ? <SkeletonTable /> : null}

      {isError ? (
        <EmptyState
          heading="Couldn't load campaigns"
          description="The request to the backend failed. Make sure it's running at localhost:8000."
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
      ) : null}

      {!isLoading && !isError && data?.items?.length === 0 ? (
        <EmptyState
          heading="No campaigns yet"
          description="Start by describing a product and Campaign Studio will research angles and generate assets for it."
          action={
            <button
              type="button"
              onClick={() => navigate('/campaigns/new')}
              className="px-4 py-2 text-sm font-medium rounded-sm bg-signal text-white hover:bg-signalDark"
            >
              Create your first campaign
            </button>
          }
        />
      ) : null}

      {!isLoading && !isError && data?.items?.length > 0 ? (
        <>
          <CampaignTable campaigns={data.items} />
          <Pagination
            limit={data.limit}
            offset={data.offset}
            count={data.count}
            onOffsetChange={(next) => updateParam('offset', String(next))}
          />
        </>
      ) : null}
    </div>
  );
}
