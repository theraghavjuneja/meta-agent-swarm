export default function Pagination({ limit, offset, count, onOffsetChange }) {
  const page = Math.floor(offset / limit) + 1;
  const hasNext = offset + limit < count;
  const hasPrev = offset > 0;
  const rangeStart = count === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + limit, count);

  return (
    <div className="flex items-center justify-between border-t border-line pt-3 mt-2 text-sm text-slate">
      <span>
        {rangeStart}–{rangeEnd} of {count}
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={!hasPrev}
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
          className="px-3 py-1.5 rounded-sm border border-line disabled:opacity-40 disabled:cursor-not-allowed hover:border-ink"
        >
          Previous
        </button>
        <span className="text-ink">Page {page}</span>
        <button
          type="button"
          disabled={!hasNext}
          onClick={() => onOffsetChange(offset + limit)}
          className="px-3 py-1.5 rounded-sm border border-line disabled:opacity-40 disabled:cursor-not-allowed hover:border-ink"
        >
          Next
        </button>
      </div>
    </div>
  );
}
