import { useNavigate } from 'react-router-dom';
import StatusBadge from './StatusBadge';
import { CAMPAIGN_STATUS_META, formatDate } from '../lib/status';

export default function CampaignTable({ campaigns }) {
  const navigate = useNavigate();

  return (
    <table className="w-full text-left text-sm">
      <thead>
        <tr className="border-b border-line text-slate">
          <th className="py-2.5 font-medium">Product</th>
          <th className="py-2.5 font-medium">Status</th>
          <th className="py-2.5 font-medium">Created</th>
          <th className="py-2.5 font-medium">Updated</th>
        </tr>
      </thead>
      <tbody>
        {campaigns.map((campaign) => (
          <tr
            key={campaign.id}
            onClick={() => navigate(`/campaigns/${campaign.id}`)}
            className="border-b border-line cursor-pointer hover:bg-black/[0.02]"
          >
            <td className="py-3 font-medium text-ink">{campaign.product_name || '—'}</td>
            <td className="py-3">
              <StatusBadge meta={CAMPAIGN_STATUS_META[campaign.status]} />
            </td>
            <td className="py-3 text-slate">{formatDate(campaign.created_at)}</td>
            <td className="py-3 text-slate">{formatDate(campaign.updated_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
