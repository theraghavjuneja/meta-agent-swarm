export const CAMPAIGN_STATUSES = [
  'draft',
  'researching',
  'awaiting_angle_selection',
  'generating_spec',
  'generating_assets',
  'completed',
  'completed_with_errors',
  'failed',
];

export const CAMPAIGN_STATUS_META = {
  draft: { label: 'Draft', dot: 'bg-status-gray', text: 'text-status-gray' },
  researching: { label: 'Researching', dot: 'bg-status-blue', text: 'text-status-blue' },
  awaiting_angle_selection: {
    label: 'Awaiting angle selection',
    dot: 'bg-status-amber',
    text: 'text-status-amber',
  },
  generating_spec: { label: 'Generating spec', dot: 'bg-status-violet', text: 'text-status-violet' },
  generating_assets: {
    label: 'Generating assets',
    dot: 'bg-status-indigo',
    text: 'text-status-indigo',
  },
  completed: { label: 'Completed', dot: 'bg-status-green', text: 'text-status-green' },
  completed_with_errors: {
    label: 'Completed with errors',
    dot: 'bg-status-orange',
    text: 'text-status-orange',
  },
  failed: { label: 'Failed', dot: 'bg-status-red', text: 'text-status-red' },
};

export const ASSET_STATUS_META = {
  pending: { label: 'Pending', dot: 'bg-status-gray', text: 'text-status-gray' },
  generating: { label: 'Generating', dot: 'bg-status-blue', text: 'text-status-blue' },
  completed: { label: 'Completed', dot: 'bg-status-green', text: 'text-status-green' },
  failed: { label: 'Failed', dot: 'bg-status-red', text: 'text-status-red' },
};

export const ASSET_TYPE_LABELS = {
  hero_image: 'Hero image',
  ad_1x1: 'Square ad (1:1)',
  ad_9x16: 'Vertical ad (9:16)',
  video: 'Video',
};

// A campaign in one of these states is still being worked on by the workflow,
// so list/detail views should keep polling until it lands on a terminal state.
const TERMINAL_CAMPAIGN_STATUSES = new Set(['completed', 'failed', 'completed_with_errors']);

export function isCampaignInProgress(status) {
  return status ? !TERMINAL_CAMPAIGN_STATUSES.has(status) : false;
}

export function isAssetInProgress(status) {
  return status === 'pending' || status === 'generating';
}

export function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatCurrency(value) {
  if (value === null || value === undefined) return '—';
  return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(value);
}
