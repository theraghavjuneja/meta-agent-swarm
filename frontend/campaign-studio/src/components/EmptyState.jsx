export default function EmptyState({ icon, heading, description, action }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-20 px-6 border border-dashed border-line rounded-md">
      {icon ? <div className="text-3xl mb-3">{icon}</div> : null}
      <h3 className="font-display text-base font-semibold text-ink">{heading}</h3>
      {description ? <p className="mt-1.5 text-sm text-slate max-w-sm">{description}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}
