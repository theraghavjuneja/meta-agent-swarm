export function SkeletonBlock({ className = '' }) {
  return <div className={`animate-pulse-soft bg-line rounded-sm ${className}`} />;
}

export function SkeletonTable({ rows = 6 }) {
  return (
    <div className="space-y-3">
      <SkeletonBlock className="h-4 w-full" />
      {Array.from({ length: rows }).map((_, i) => (
        <SkeletonBlock key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

export function SkeletonLine({ width = 'w-full', className = '' }) {
  return <SkeletonBlock className={`h-4 ${width} ${className}`} />;
}
