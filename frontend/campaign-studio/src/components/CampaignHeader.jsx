import StatusBadge from './StatusBadge';
import { CAMPAIGN_STATUS_META, formatDate } from '../lib/status';

export default function CampaignHeader({ campaign }) {
  return (
    <div className="mb-6">
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="font-display text-2xl font-semibold text-ink">{campaign.product_name}</h1>
        <StatusBadge meta={CAMPAIGN_STATUS_META[campaign.status]} />
      </div>
      <p className="mt-1 text-sm text-slate">
        Created {formatDate(campaign.created_at)} · Updated {formatDate(campaign.updated_at)}
      </p>
    </div>
  );
}
