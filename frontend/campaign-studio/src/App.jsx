import { Navigate, Route, Routes } from 'react-router-dom';
import DashboardLayout from './layouts/DashboardLayout';
import CampaignLayout from './layouts/CampaignLayout';
import CampaignList from './pages/CampaignList';
import CampaignCreate from './pages/CampaignCreate';
import CampaignResearch from './pages/CampaignResearch';
import CampaignSpec from './pages/CampaignSpec';
import CampaignAssets from './pages/CampaignAssets';
import CampaignUsage from './pages/CampaignUsage';
import NotFound from './pages/NotFound';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/campaigns" replace />} />

      <Route element={<DashboardLayout />}>
        <Route path="/campaigns" element={<CampaignList />} />
        <Route path="/campaigns/new" element={<CampaignCreate />} />

        <Route path="/campaigns/:id" element={<CampaignLayout />}>
          <Route index element={<Navigate to="research" replace />} />
          <Route path="research" element={<CampaignResearch />} />
          <Route path="spec" element={<CampaignSpec />} />
          <Route path="assets" element={<CampaignAssets />} />
          <Route path="usage" element={<CampaignUsage />} />
        </Route>

        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
