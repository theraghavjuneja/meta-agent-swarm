import { NavLink, useParams } from 'react-router-dom';

const tabs = [
  { path: 'research', label: 'Research' },
  { path: 'spec', label: 'Creative Spec' },
  { path: 'assets', label: 'Assets' },
  { path: 'usage', label: 'Usage' },
];

export default function TabsNav() {
  const { id } = useParams();
  return (
    <div className="flex items-center gap-1 border-b border-line mb-6">
      {tabs.map((tab) => (
        <NavLink
          key={tab.path}
          to={`/campaigns/${id}/${tab.path}`}
          className={({ isActive }) =>
            `px-3 py-2.5 text-sm font-medium border-b-2 -mb-px ${
              isActive
                ? 'border-signal text-ink'
                : 'border-transparent text-slate hover:text-ink'
            }`
          }
        >
          {tab.label}
        </NavLink>
      ))}
    </div>
  );
}
