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

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RESTARTING = [0, 502, 503, 504]; // the server is restarting (a deploy takes a few seconds)

export async function api(path, { method = "GET", body } = {}) {
  // Reads are safe to repeat, so ride out a restart instead of showing an error. Writes are never repeated.
  const tries = method === "GET" ? 6 : 1;
  for (let attempt = 1; ; attempt++) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch {
      if (attempt < tries) {
        await sleep(700 * attempt);
        continue;
      }
      throw new ApiError("Can't reach Lark right now. Check the connection.", 0);
    }
    if (RESTARTING.includes(res.status) && attempt < tries) {
      await sleep(700 * attempt);
      continue;
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(errorMessage(data, res.status), res.status);
    return data;
  }
}
