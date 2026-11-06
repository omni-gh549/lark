export const SETTINGS_PAGES = [
  { id: "model", label: "Model" },
  { id: "swarm", label: "Agent Swarm" },
  { id: "keys", label: "API keys" },
  { id: "usage", label: "Usage" },
  { id: "search", label: "Web search" },
  { id: "sandbox", label: "Sandbox" },
  { id: "notion", label: "Notion" },
  { id: "telegram", label: "Telegram" },
  { id: "appearance", label: "Appearance" },
];
export const pageLabel = (id) => SETTINGS_PAGES.find((p) => p.id === id)?.label ?? "Settings";
