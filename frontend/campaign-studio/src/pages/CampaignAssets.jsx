import { useOutletContext } from 'react-router-dom';
import { useAssets, useRetryAsset } from '../hooks/useCampaigns';
import MediaCard from '../components/MediaCard';
import EmptyState from '../components/EmptyState';
import { SkeletonBlock } from '../components/Skeleton';
import { isAssetInProgress } from '../lib/status';

export default function CampaignAssets() {
  const { campaign } = useOutletContext();
  const { data, isLoading, isError, refetch } = useAssets(campaign.id, campaign.status);
  const retryAsset = useRetryAsset(campaign.id);

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <SkeletonBlock key={i} className="h-64 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <EmptyState
        heading="Couldn't load assets"
        description="Something went wrong fetching this campaign's assets."
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

  if (!data?.items?.length) {
    return (
      <EmptyState
        heading="No assets yet"
        description="Assets are generated once a creative angle is selected and the spec is approved."
      />
    );
  }

  const hero = data.items.find((item) => item.asset_type === 'hero_image');
  const heroCompleted = hero ? hero.status === 'completed' : true;

  return (
    <div>
      <p className="text-xs text-slate mb-4">Spec version {data.current_spec_version}</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {data.items.map((asset) => {
          const inProgress = isAssetInProgress(asset.status);
          const dependsOnHero = asset.asset_type !== 'hero_image';
          const blockedByHero = dependsOnHero && !heroCompleted;

          let retryBlockedReason;
          if (inProgress) retryBlockedReason = 'This asset is already generating.';
          else if (blockedByHero) retryBlockedReason = 'The hero image needs to complete first.';

          return (
            <MediaCard
              key={asset.id}
              asset={asset}
              isRetrying={retryAsset.isPending && retryAsset.variables === asset.id}
              retryBlocked={inProgress || blockedByHero}
              retryBlockedReason={retryBlockedReason}
              onRetry={(assetId) => retryAsset.mutate(assetId)}
            />
          );
        })}
      </div>
    </div>
  );
}
