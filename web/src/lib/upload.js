import { ApiError, errorMessage } from "./api.js";

const MAX_SIDE = 1600;

// Phone photos are huge; shrink them before upload so they send fast and cost fewer tokens.
async function shrink(file) {
  if (file.type === "image/gif" || file.type === "image/svg+xml") return file;
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && file.size < 1_500_000) return file;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const png = file.type === "image/png" && canvas.width * canvas.height < 1_200_000;
    return await new Promise((ok) => canvas.toBlob((b) => ok(b ?? file), png ? "image/png" : "image/jpeg", 0.88));
  } catch {
    return file;
  }
}

export async function uploadImage(file) {
  const body = await shrink(file);
  const res = await fetch("/api/uploads", { method: "POST", headers: { "Content-Type": body.type || "application/octet-stream" }, body });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(errorMessage(data, res.status), res.status);
  return data.id;
}
