const REVIEWS = '/api/v1/reviews';

export const isReviewId = (value) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);

export class ApiError extends Error {
  constructor(message, reviewId = null) {
    super(message);
    this.reviewId = reviewId;
  }
}

async function request(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError('Cannot reach the API. Check that the backend is running. If a submission was interrupted, it may still be running on the server.');
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new ApiError(`The API returned an unreadable response (HTTP ${response.status}).`);
  }
  if (!response.ok) {
    const detail = data.detail;
    const message = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((item) => `${item.loc?.slice(1).join('.') || 'Request'}: ${item.msg}`).join('; ')
      : detail?.message || `Request failed (HTTP ${response.status}).`;
    const id = detail?.review_id;
    throw new ApiError(message, typeof id === 'string' && isReviewId(id) ? id : null);
  }
  return data;
}

export const createReview = (repository, number) => request(REVIEWS, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ repository, pull_request_number: number }),
});
export const getReview = (id) => request(`${REVIEWS}/${encodeURIComponent(id)}`);
export const resumeReview = (id) => request(`${REVIEWS}/${encodeURIComponent(id)}/resume`, { method: 'POST' });
