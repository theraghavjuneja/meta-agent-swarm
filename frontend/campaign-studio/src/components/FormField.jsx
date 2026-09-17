export function FormInput({ label, name, register, error, maxLength, placeholder, hint }) {
  return (
    <div className="mb-5">
      <label htmlFor={name} className="block text-sm font-medium text-ink mb-1.5">
        {label}
      </label>
      <input
        id={name}
        placeholder={placeholder}
        maxLength={maxLength}
        {...register(name)}
        className={`w-full rounded-sm border px-3 py-2 text-sm text-ink bg-white focus:border-ink ${
          error ? 'border-status-red' : 'border-line'
        }`}
      />
      {hint && !error ? <p className="mt-1 text-xs text-slate">{hint}</p> : null}
      {error ? <p className="mt-1 text-xs text-status-red">{error.message}</p> : null}
    </div>
  );
}

export function FormTextarea({ label, name, register, error, rows = 4, placeholder, hint }) {
  return (
    <div className="mb-5">
      <label htmlFor={name} className="block text-sm font-medium text-ink mb-1.5">
        {label}
      </label>
      <textarea
        id={name}
        rows={rows}
        placeholder={placeholder}
        {...register(name)}
        className={`w-full rounded-sm border px-3 py-2 text-sm text-ink bg-white focus:border-ink resize-y ${
          error ? 'border-status-red' : 'border-line'
        }`}
      />
      {hint && !error ? <p className="mt-1 text-xs text-slate">{hint}</p> : null}
      {error ? <p className="mt-1 text-xs text-status-red">{error.message}</p> : null}
    </div>
  );
}
