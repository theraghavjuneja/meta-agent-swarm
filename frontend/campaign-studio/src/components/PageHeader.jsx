export default function PageHeader({ title, description, actions }) {
  return (
    <div className="flex items-start justify-between gap-4 mb-8">
      <div>
        <h1 className="font-display text-2xl font-semibold text-ink">{title}</h1>
        {description ? <p className="mt-1 text-sm text-slate max-w-xl">{description}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2 shrink-0">{actions}</div> : null}
    </div>
  );
}
