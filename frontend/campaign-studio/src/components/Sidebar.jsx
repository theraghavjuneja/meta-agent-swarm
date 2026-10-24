import { NavLink } from 'react-router-dom';

const navItems = [{ to: '/campaigns', label: 'Campaigns' }];

export default function Sidebar() {
  return (
    <aside className="fixed inset-y-0 left-0 w-56 bg-ink text-white flex flex-col">
      <div className="px-5 py-6">
        <span className="font-display text-lg font-semibold tracking-tight">Campaign Studio</span>
      </div>
      <nav className="flex-1 px-3 space-y-1">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex items-center gap-2 rounded-sm px-3 py-2 text-sm border-l-2 transition-colors ${
                isActive
                  ? 'border-signal bg-white/5 text-white'
                  : 'border-transparent text-white/60 hover:text-white hover:bg-white/5'
              }`
            }
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="px-5 py-4 text-xs text-white/35">Connected to localhost:8000</div>
    </aside>
  );
}
