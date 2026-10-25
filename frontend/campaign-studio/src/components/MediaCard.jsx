import { useState } from 'react';
import StatusBadge from './StatusBadge';
import { ASSET_STATUS_META, ASSET_TYPE_LABELS, isAssetInProgress } from '../lib/status';

export default function MediaCard({ asset, onRetry, isRetrying, retryBlocked, retryBlockedReason }) {
  const [confirming, setConfirming] = useState(false);
  const canRetry = (asset.status === 'failed' || asset.status === 'completed') && !retryBlocked;

  return (
    <div className="rounded-md border border-line overflow-hidden flex flex-col">
      <div className="aspect-video bg-paper border-b border-line flex items-center justify-center">
        {asset.status === 'completed' && asset.preview_url ? (
          asset.asset_type === 'video' ? (
            <video src={asset.preview_url} controls className="h-full w-full object-cover" />
          ) : (
            <img src={asset.preview_url} alt={ASSET_TYPE_LABELS[asset.asset_type]} className="h-full w-full object-cover" />
          )
        ) : (
          <span className="text-xs text-slate">
            {isAssetInProgress(asset.status) ? 'Generating…' : 'No preview yet'}
          </span>
        )}
      </div>
      <div className="p-3.5 flex-1 flex flex-col">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium text-ink">{ASSET_TYPE_LABELS[asset.asset_type]}</span>
          <StatusBadge meta={ASSET_STATUS_META[asset.status]} />
        </div>
        <p className="text-xs text-slate mt-1">
          {asset.width && asset.height ? `${asset.width}×${asset.height}` : null}
          {asset.duration_seconds ? ` · ${asset.duration_seconds}s` : null}
        </p>
        {asset.status === 'failed' && asset.last_error ? (
          <p className="text-xs text-status-red mt-2">{asset.last_error}</p>
        ) : null}
        <div className="mt-auto pt-3">
          {confirming ? (
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate">Retry this asset?</span>
              <button
                type="button"
                onClick={() => {
                  onRetry(asset.id);
                  setConfirming(false);
                }}
                className="px-2.5 py-1 text-xs font-medium rounded-sm bg-ink text-white hover:bg-ink/90"
              >
                Confirm
              </button>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                className="px-2.5 py-1 text-xs text-slate hover:text-ink"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              type="button"
              disabled={!canRetry || isRetrying}
              onClick={() => setConfirming(true)}
              title={!canRetry ? retryBlockedReason : undefined}
              className="px-3 py-1.5 text-sm font-medium rounded-sm border border-line hover:border-ink disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {isRetrying ? 'Retrying…' : 'Retry'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
