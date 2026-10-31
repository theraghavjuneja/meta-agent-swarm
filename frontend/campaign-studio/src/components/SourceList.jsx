export default function SourceList({ sources }) {
  if (!sources?.length) {
    return <p className="text-sm text-slate">No sources gathered yet.</p>;
  }

  return (
    <ul className="space-y-3">
      {sources.map((source, index) => (
        <li key={source.id || index} className="border-b border-line pb-3 last:border-0">
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer"
            className="text-sm font-medium text-ink hover:text-signal break-words"
          >
            {source.title || source.url}
          </a>
          {source.snippet ? (
            <p className="text-sm text-slate mt-0.5 line-clamp-2">{source.snippet}</p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
