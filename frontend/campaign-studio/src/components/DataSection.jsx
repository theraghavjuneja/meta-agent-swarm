export default function DataSection({ label, children }) {
  return (
    <div className="py-4 border-b border-line last:border-0">
      <h3 className="text-xs font-medium text-slate mb-1.5">{label}</h3>
      <div className="text-sm text-ink leading-relaxed">{children}</div>
    </div>
  );
}
