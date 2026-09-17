import { CAMPAIGN_STATUS_META, CAMPAIGN_STATUSES } from '../lib/status';

export default function StatusFilter({ value, onChange }) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="rounded-sm border border-line bg-white px-3 py-2 text-sm text-ink focus:border-ink"
    >
      <option value="all">All statuses</option>
      {CAMPAIGN_STATUSES.map((status) => (
        <option key={status} value={status}>
          {CAMPAIGN_STATUS_META[status].label}
        </option>
      ))}
    </select>
  );
}
