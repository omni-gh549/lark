// Stroke paths (24x24 viewBox, drawn with the global svg styles) for each tool; unknown tools get the wrench.
const ICONS = {
  web_search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  run_command: '<rect x="3.5" y="5" width="17" height="14" rx="3"/><path d="m8 10 3 2-3 2M13 14.5h3.5"/>',
  read_file: '<path d="M7 3.5h6.5L18 8v12.5H7z"/><path d="M13 3.5V8h5M9.5 12.5h5M9.5 16h5"/>',
  write_file: '<path d="M7 3.5h6.5L18 8v4M7 3.5v17h5"/><path d="M13 3.5V8h5M13.5 20.5l.7-3 5.3-5.3 2.3 2.3-5.3 5.3z"/>',
  browser: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.4 3.6 5.2 3.6 8.500S14.400 18.100 12 20.500M12 3.500C9.600 5.900 8.400 8.700 8.400 12s1.200 6.100 3.600 8.500"/>',
  subagent: '<circle cx="12" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M12 8.5v3.5M12 12 7 15.8M12 12l5 3.8"/>',
};
const FALLBACK = '<path d="M14.5 6.5a4 4 0 0 0 4.9 4.9L10 20.8a2.1 2.1 0 0 1-3-3z"/><path d="M14.5 6.5l3-3"/>';

export const toolIcon = (name) => ICONS[name] ?? FALLBACK;
