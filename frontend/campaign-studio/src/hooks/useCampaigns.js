import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  createCampaign,
  getAssets,
  getCampaign,
  getResearch,
  getSpec,
  getUsage,
  listCampaigns,
  retryAsset,
  selectAngle,
} from '../api/campaigns';
import { extractErrorMessage } from '../api/client';
import { queryKeys } from '../lib/queryKeys';
import { isAssetInProgress, isCampaignInProgress } from '../lib/status';

const POLL_INTERVAL_MS = 5000;

export function useCampaignList(filters) {
  return useQuery({
    queryKey: queryKeys.campaigns(filters),
    queryFn: () => listCampaigns(filters),
    placeholderData: (previous) => previous,
  });
}

export function useCampaign(campaignId) {
  return useQuery({
    queryKey: queryKeys.campaign(campaignId),
    queryFn: () => getCampaign(campaignId),
    enabled: Boolean(campaignId),
    refetchInterval: (query) =>
      isCampaignInProgress(query.state.data?.status) ? POLL_INTERVAL_MS : false,
  });
}

export function useCreateCampaign() {
  return useMutation({
    mutationFn: createCampaign,
    onSuccess: () => toast.success('Campaign created successfully!'),
    onError: (error) => toast.error(extractErrorMessage(error, 'Could not create the campaign.')),
  });
}

export function useResearch(campaignId, campaignStatus) {
  return useQuery({
    queryKey: queryKeys.research(campaignId),
    queryFn: () => getResearch(campaignId),
    enabled: Boolean(campaignId),
    refetchInterval: (query) => {
      const runStatus = query.state.data?.run?.status;
      if (runStatus === 'running') return POLL_INTERVAL_MS;
      return isCampaignInProgress(campaignStatus) ? POLL_INTERVAL_MS : false;
    },
  });
}

export function useSelectAngle(campaignId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (angleId) => selectAngle(campaignId, angleId),
    onSuccess: () => {
      toast.success('Angle selected successfully!');
      queryClient.invalidateQueries({ queryKey: queryKeys.research(campaignId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.campaign(campaignId) });
    },
    onError: (error) => toast.error(extractErrorMessage(error, 'Could not select that angle.')),
  });
}

export function useSpec(campaignId, campaignStatus) {
  return useQuery({
    queryKey: queryKeys.spec(campaignId),
    queryFn: () => getSpec(campaignId),
    enabled: Boolean(campaignId),
    retry: (failureCount, error) => (error?.response?.status === 404 ? false : failureCount < 1),
    refetchInterval: () => (campaignStatus === 'generating_spec' ? POLL_INTERVAL_MS : false),
  });
}

export function useAssets(campaignId, campaignStatus) {
  return useQuery({
    queryKey: queryKeys.assets(campaignId),
    queryFn: () => getAssets(campaignId),
    enabled: Boolean(campaignId),
    refetchInterval: (query) => {
      const items = query.state.data?.items || [];
      const anyInProgress = items.some((asset) => isAssetInProgress(asset.status));
      return anyInProgress || isCampaignInProgress(campaignStatus) ? POLL_INTERVAL_MS : false;
    },
  });
}

export function useRetryAsset(campaignId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (assetId) => retryAsset(campaignId, assetId),
    onSuccess: () => {
      toast.success('Retry started.');
      queryClient.invalidateQueries({ queryKey: queryKeys.assets(campaignId) });
    },
    onError: (error) => toast.error(extractErrorMessage(error, 'Could not retry this asset.')),
  });
}

export function useUsage(campaignId) {
  return useQuery({
    queryKey: queryKeys.usage(campaignId),
    queryFn: () => getUsage(campaignId),
    enabled: Boolean(campaignId),
  });
}
