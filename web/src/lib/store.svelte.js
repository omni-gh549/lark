import { api, ApiError } from "./api.js";

export const app = $state({
  settings: null, // null until loaded
  locked: false, // server wants a password
  blocked: "", // server refused to answer
  panels: [], // [{ id, type, props }] rendered by the panel registry
});

export async function loadSettings() {
  try {
    app.settings = await api("/api/settings");
    app.locked = false;
    app.blocked = "";
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) app.locked = true;
    else app.blocked = e.message;
  }
}

export function configured() {
  const s = app.settings;
  if (!s) return true; // don't flash the notice while loading
  return Boolean(s.providers[s.provider].key_hint && s.models[s.provider]);
}
