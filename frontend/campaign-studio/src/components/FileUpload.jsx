import { useRef, useState } from 'react';

const MAX_BYTES = 5 * 1024 * 1024;
const ACCEPTED = ['image/jpeg', 'image/png', 'image/webp'];

export default function FileUpload({ label, value, onChange, error, hint }) {
  const inputRef = useRef(null);
  const [localError, setLocalError] = useState(null);
  const previewUrl = value ? URL.createObjectURL(value) : null;

  function handleFile(file) {
    if (!file) {
      onChange(null);
      return;
    }
    if (!ACCEPTED.includes(file.type)) {
      setLocalError('Only JPEG, PNG, or WEBP images are supported.');
      return;
    }
    if (file.size > MAX_BYTES) {
      setLocalError('Image must be 5MB or smaller.');
      return;
    }
    setLocalError(null);
    onChange(file);
  }

  const displayError = error || localError;

  return (
    <div className="mb-5">
      <label className="block text-sm font-medium text-ink mb-1.5">{label}</label>
      {hint ? <p className="mb-2 text-xs text-slate">{hint}</p> : null}
      <div
        className={`flex items-center gap-4 rounded-sm border border-dashed px-4 py-4 ${
          displayError ? 'border-status-red' : 'border-line'
        }`}
      >
        {previewUrl ? (
          <img src={previewUrl} alt="Reference preview" className="h-16 w-16 object-cover rounded-sm" />
        ) : (
          <div className="h-16 w-16 rounded-sm bg-paper border border-line flex items-center justify-center text-xs text-slate">
            No file
          </div>
        )}
        <div className="flex-1">
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED.join(',')}
            className="hidden"
            onChange={(event) => handleFile(event.target.files?.[0] || null)}
          />
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              className="px-3 py-1.5 text-sm font-medium rounded-sm border border-line hover:border-ink"
            >
              Choose image
            </button>
            {value ? (
              <button
                type="button"
                onClick={() => handleFile(null)}
                className="text-sm text-slate hover:text-status-red"
              >
                Remove
              </button>
            ) : null}
          </div>
          <p className="mt-1 text-xs text-slate">JPEG, PNG or WEBP, up to 5MB.</p>
        </div>
      </div>
      {displayError ? <p className="mt-1 text-xs text-status-red">{displayError}</p> : null}
    </div>
  );
}
