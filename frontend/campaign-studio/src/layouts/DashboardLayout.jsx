import { Outlet } from 'react-router-dom';
import Sidebar from '../components/Sidebar';
import ErrorBoundary from '../components/ErrorBoundary';

export default function DashboardLayout() {
  return (
    <div className="min-h-screen bg-paper">
      <Sidebar />
      <main className="pl-56">
        <div className="max-w-5xl mx-auto px-8 py-10">
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </div>
      </main>
    </div>
  );
}
