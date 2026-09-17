import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export const apiClient = axios.create({
  baseURL: `${API_BASE_URL}`,
});

// Normalizes backend error payloads into a single readable message.
// The backend returns { message } for DomainError / InfrastructureError,
// and a `detail` array for FastAPI 422 validation errors.
export function extractErrorMessage(error, fallback = 'Something went wrong. Please try again.') {
  const data = error?.response?.data;
  if (!data) return error?.message || fallback;

  if (typeof data.message === 'string') return data.message;

  if (Array.isArray(data.detail)) {
    return data.detail
      .map((item) => {
        const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : null;
        return field ? `${field}: ${item.msg}` : item.msg;
      })
      .join(' · ');
  }

  if (typeof data.detail === 'string') return data.detail;

  return fallback;
}
