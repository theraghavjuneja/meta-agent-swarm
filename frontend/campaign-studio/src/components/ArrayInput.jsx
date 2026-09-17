export default function ArrayInput({ label, values, onChange, placeholder, hint }) {
  function updateAt(index, value) {
    const next = [...values];
    next[index] = value;
    onChange(next);
  }

  function removeAt(index) {
    onChange(values.filter((_, i) => i !== index));
  }

  function add() {
    onChange([...values, '']);
  }

  return (
    <div className="mb-5">
      <label className="block text-sm font-medium text-ink mb-1.5">{label}</label>
      {hint ? <p className="mb-2 text-xs text-slate">{hint}</p> : null}
      <div className="space-y-2">
        {values.map((value, index) => (
          <div key={index} className="flex items-center gap-2">
            <input
              value={value}
              placeholder={placeholder}
              onChange={(event) => updateAt(index, event.target.value)}
              className="flex-1 rounded-sm border border-line px-3 py-2 text-sm text-ink bg-white focus:border-ink"
            />
            <button
              type="button"
              onClick={() => removeAt(index)}
              className="px-2.5 py-2 text-sm text-slate hover:text-status-red"
              aria-label="Remove claim"
            >
              Remove
            </button>
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={add}
        className="mt-2 text-sm font-medium text-signal hover:text-signalDark"
      >
        + Add claim
      </button>
    </div>
  );
}
