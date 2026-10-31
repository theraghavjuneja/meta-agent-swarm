import { useOutletContext } from 'react-router-dom';
import { useSpec } from '../hooks/useCampaigns';
import DataSection from '../components/DataSection';
import ColorSwatch from '../components/ColorSwatch';
import EmptyState from '../components/EmptyState';
import { SkeletonLine } from '../components/Skeleton';

export default function CampaignSpec() {
  const { campaign } = useOutletContext();
  const { data, isLoading, isError, error } = useSpec(campaign.id, campaign.status);

  if (isLoading) {
    return (
      <div className="space-y-3">
        <SkeletonLine width="w-full" />
        <SkeletonLine width="w-5/6" />
        <SkeletonLine width="w-2/3" />
      </div>
    );
  }

  const notGeneratedYet = isError && error?.response?.status === 404;

  if (notGeneratedYet) {
    return (
      <EmptyState
        heading="Creative specification is currently being generated"
        description="This page will update automatically once the spec is ready."
      />
    );
  }

  if (isError) {
    return (
      <EmptyState
        heading="Couldn't load the creative spec"
        description="Something went wrong fetching this campaign's spec."
      />
    );
  }

  return (
    <div className="max-w-2xl">
      <DataSection label="Hook">
        <p className="font-display text-lg font-semibold">{data.hook}</p>
      </DataSection>
      <DataSection label="Approved copy">
        <p className="whitespace-pre-wrap">{data.approved_copy}</p>
      </DataSection>
      <DataSection label="Call to action">{data.cta}</DataSection>
      <DataSection label="Scene description">
        <p className="whitespace-pre-wrap">{data.scene_description}</p>
      </DataSection>
      <DataSection label="Composition guidance">
        <p className="whitespace-pre-wrap">{data.composition_guidance}</p>
      </DataSection>
      <DataSection label="Palette">
        <ColorSwatch colors={data.palette} />
      </DataSection>
      {data.product_identity ? (
        <DataSection label="Product identity">
          <pre className="text-xs font-mono bg-white border border-line rounded-sm p-3 overflow-x-auto">
            {JSON.stringify(data.product_identity, null, 2)}
          </pre>
        </DataSection>
      ) : null}
      {data.video_outline ? (
        <DataSection label="Video outline">
          <pre className="text-xs font-mono bg-white border border-line rounded-sm p-3 overflow-x-auto">
            {JSON.stringify(data.video_outline, null, 2)}
          </pre>
        </DataSection>
      ) : null}
      <p className="text-xs text-slate mt-4">Version {data.version}</p>
    </div>
  );
}
