export default function StatusBadge({ meta }) {
  if (!meta) return <span className="text-slate text-sm">—</span>;
  return (
    <span className={`inline-flex items-center gap-1.5 text-sm font-medium ${meta.text}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
      {meta.label}
    </span>
  );
}
