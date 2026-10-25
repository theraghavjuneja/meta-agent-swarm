import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useNavigate } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import { FormInput, FormTextarea } from '../components/FormField';
import ArrayInput from '../components/ArrayInput';
import FileUpload from '../components/FileUpload';
import { useCreateCampaign } from '../hooks/useCampaigns';

const schema = z.object({
  product_name: z.string().min(1, 'Product name is required.').max(255, 'Must be 255 characters or fewer.'),
  product_description: z.string().min(1, 'Product description is required.'),
  target_audience: z.string().min(1, 'Target audience is required.'),
  objective: z.string().min(1, 'Objective is required.').max(255, 'Must be 255 characters or fewer.'),
  tone: z.string().min(1, 'Tone is required.').max(100, 'Must be 100 characters or fewer.'),
  cta: z.string().min(1, 'Call to action is required.').max(255, 'Must be 255 characters or fewer.'),
});

export default function CampaignCreate() {
  const navigate = useNavigate();
  const createCampaign = useCreateCampaign();
  const [claims, setClaims] = useState([]);
  const [referenceImage, setReferenceImage] = useState(null);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm({ resolver: zodResolver(schema) });

  async function onSubmit(values) {
    try {
      const result = await createCampaign.mutateAsync({
        ...values,
        verified_claims: claims.filter((claim) => claim.trim().length > 0),
        reference_image: referenceImage,
      });
      navigate(`/campaigns/${result.campaign.id}/research`);
    } catch {
      // Error toast already shown by the mutation's onError handler.
    }
  }

  return (
    <div>
      <PageHeader
        title="Create campaign"
        description="Describe the product and Campaign Studio will research creative angles and generate assets."
      />

      <form onSubmit={handleSubmit(onSubmit)} className="max-w-2xl">
        <FormInput
          label="Product name"
          name="product_name"
          register={register}
          error={errors.product_name}
          maxLength={255}
          placeholder="e.g. Aurora Trail Running Shoes"
        />
        <FormTextarea
          label="Product description"
          name="product_description"
          register={register}
          error={errors.product_description}
          rows={4}
          placeholder="What is it, what does it do, what makes it different?"
        />
        <FormTextarea
          label="Target audience"
          name="target_audience"
          register={register}
          error={errors.target_audience}
          rows={2}
          placeholder="Who is this campaign speaking to?"
        />
        <FormInput
          label="Objective"
          name="objective"
          register={register}
          error={errors.objective}
          maxLength={255}
          placeholder="e.g. Drive pre-orders for launch week"
        />
        <FormInput
          label="Tone"
          name="tone"
          register={register}
          error={errors.tone}
          maxLength={100}
          placeholder="e.g. Confident, energetic, a little irreverent"
        />
        <FormInput
          label="Call to action"
          name="cta"
          register={register}
          error={errors.cta}
          maxLength={255}
          placeholder="e.g. Shop the launch collection"
        />
        <ArrayInput
          label="Verified claims"
          hint="Facts about the product that are safe to state directly in ad copy."
          values={claims}
          onChange={setClaims}
          placeholder="e.g. Certified carbon-neutral shipping"
        />
        <FileUpload
          label="Reference image"
          hint="Optional. Used to ground the visual direction."
          value={referenceImage}
          onChange={setReferenceImage}
        />

        <div className="sticky bottom-0 bg-paper border-t border-line -mx-8 px-8 py-4 mt-8 flex justify-end">
          <button
            type="submit"
            disabled={createCampaign.isPending}
            className="px-4 py-2 text-sm font-medium rounded-sm bg-signal text-white hover:bg-signalDark disabled:opacity-50"
          >
            {createCampaign.isPending ? 'Creating…' : 'Create campaign'}
          </button>
        </div>
      </form>
    </div>
  );
}
