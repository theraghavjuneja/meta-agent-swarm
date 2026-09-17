import { Link } from 'react-router-dom';

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center text-center py-24 px-6">
      <h1 className="font-display text-2xl font-semibold text-ink">Page not found</h1>
      <p className="mt-1.5 text-sm text-slate">The page you're looking for doesn't exist.</p>
      <Link
        to="/campaigns"
        className="mt-4 px-4 py-2 rounded-sm bg-ink text-white text-sm font-medium hover:bg-ink/90"
      >
        Back to campaigns
      </Link>
    </div>
  );
}
