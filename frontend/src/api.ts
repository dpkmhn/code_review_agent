import type { Review } from './types';

const REVIEWS = '/api/v1/reviews';

export class ApiError extends Error {}

interface ValidationIssue { loc?: (string | number)[]; msg: string }
type ErrorDetail = string | ValidationIssue[] | { message?: string } | undefined;

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError('Cannot reach the API. Check that the backend is running.');
  }
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new ApiError(`The API returned an unreadable response (HTTP ${response.status}).`);
  }
  if (!response.ok) {
    const detail = (data as { detail?: ErrorDetail }).detail;
    const message = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((item) => `${item.loc?.slice(1).join('.') || 'Request'}: ${item.msg}`).join('; ')
      : detail?.message || `Request failed (HTTP ${response.status}).`;
    throw new ApiError(message);
  }
  return data as T;
}

export const createReview = (repository: string, number: number) => request<Review>(REVIEWS, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ repository, pull_request_number: number }),
});
