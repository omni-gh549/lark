export const SETTINGS_PAGES = [
  { id: "model", label: "Model", group: "Models" },
  { id: "swarm", label: "Agent Swarm", group: "Models" },
  { id: "keys", label: "API keys", group: "Models" },
  { id: "usage", label: "Usage", group: "Models" },
  { id: "search", label: "Web search", group: "Tools" },
  { id: "sandbox", label: "Sandbox", group: "Tools" },
  { id: "notion", label: "Notion", group: "Integrations" },
  { id: "telegram", label: "Telegram", group: "Integrations" },
  { id: "appearance", label: "Appearance", group: "App" },
];
export const pageLabel = (id) => SETTINGS_PAGES.find((p) => p.id === id)?.label ?? "Settings";
