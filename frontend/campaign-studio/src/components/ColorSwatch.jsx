export default function ColorSwatch({ colors }) {
  if (!colors?.length) return <p className="text-sm text-slate">No palette generated.</p>;
  return (
    <div className="flex flex-wrap gap-3">
      {colors.map((hex) => (
        <div key={hex} className="flex flex-col items-center gap-1.5">
          <div
            className="h-10 w-10 rounded-sm border border-line"
            style={{ backgroundColor: hex }}
          />
          <span className="text-xs font-mono text-slate">{hex}</span>
        </div>
      ))}
    </div>
  );
}
