export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export function errorMessage(data, status) {
  if (typeof data.error === "string") return data.error;
  if (Array.isArray(data.detail) && data.detail[0]?.msg) return data.detail[0].msg;
  return `Request failed (${status}).`;
}

export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(errorMessage(data, res.status), res.status);
  return data;
}
