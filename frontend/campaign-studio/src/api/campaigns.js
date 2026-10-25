import { apiClient } from './client';

export async function listCampaigns({ status, limit = 50, offset = 0 }) {
  const params = { limit, offset };
  if (status && status !== 'all') params.status = status;
  const { data } = await apiClient.get('/campaigns', { params });
  return data;
}

export async function getCampaign(campaignId) {
  const { data } = await apiClient.get(`/campaigns/${campaignId}`);
  return data;
}

export async function createCampaign(payload) {
  const hasImage = payload.reference_image instanceof File;

  if (hasImage) {
    const form = new FormData();
    form.append('product_name', payload.product_name);
    form.append('product_description', payload.product_description);
    form.append('target_audience', payload.target_audience);
    form.append('objective', payload.objective);
    form.append('tone', payload.tone);
    form.append('cta', payload.cta);
    (payload.verified_claims || []).forEach((claim) => form.append('verified_claims', claim));
    form.append('reference_image', payload.reference_image);

    const { data } = await apiClient.post('/campaigns', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return data;
  }

  const { reference_image, ...jsonPayload } = payload;
  const { data } = await apiClient.post('/campaigns', jsonPayload);
  return data;
}

export async function getResearch(campaignId) {
  const { data } = await apiClient.get(`/campaigns/${campaignId}/research`);
  return data;
}

export async function selectAngle(campaignId, angleId) {
  const { data } = await apiClient.post(`/campaigns/${campaignId}/select-angle`, {
    angle_id: angleId,
  });
  return data;
}

export async function getSpec(campaignId) {
  const { data } = await apiClient.get(`/campaigns/${campaignId}/spec`);
  return data;
}

export async function getAssets(campaignId) {
  const { data } = await apiClient.get(`/campaigns/${campaignId}/assets`);
  return data;
}

export async function retryAsset(campaignId, assetId) {
  const { data } = await apiClient.post(`/campaigns/${campaignId}/assets/${assetId}/retry`);
  return data;
}

export async function getUsage(campaignId) {
  const { data } = await apiClient.get(`/campaigns/${campaignId}/usage`);
  return data;
}
