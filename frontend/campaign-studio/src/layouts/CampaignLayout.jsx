import { useEffect } from 'react';
import { Outlet, useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';
import { useCampaign } from '../hooks/useCampaigns';
import CampaignHeader from '../components/CampaignHeader';
import TabsNav from '../components/TabsNav';
import { SkeletonLine } from '../components/Skeleton';

export default function CampaignLayout() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data: campaign, isLoading, isError, error } = useCampaign(id);

  useEffect(() => {
    if (isError && error?.response?.status === 404) {
      toast.error('Campaign not found.');
      navigate('/campaigns', { replace: true });
    }
  }, [isError, error, navigate]);

  if (isLoading) {
    return (
      <div>
        <SkeletonLine width="w-64" className="h-7 mb-2" />
        <SkeletonLine width="w-40" className="mb-6" />
        <SkeletonLine width="w-full" className="h-9" />
      </div>
    );
  }

  if (!campaign) return null;

  return (
    <div>
      <CampaignHeader campaign={campaign} />
      <TabsNav />
      <Outlet context={{ campaign }} />
    </div>
  );
}
