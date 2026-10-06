// Layout prototype: view switching, panel and menu toggles, local-only composer.
const $ = (s) => document.querySelector(s);

const tabs = { chat: $("#tab-chat"), projects: $("#tab-projects") };
const views = { chat: $("#view-chat"), projects: $("#view-projects") };
function show(name) {
  for (const k in tabs) {
    tabs[k].setAttribute("aria-selected", String(k === name));
    views[k].hidden = k !== name;
  }
}
tabs.chat.onclick = () => show("chat");
tabs.projects.onclick = () => show("projects");

function toggle(btn, el) {
  const open = el.hidden;
  el.hidden = !open;
  btn.setAttribute("aria-expanded", String(open));
}
const panelBtn = $("#panel-toggle"), panels = $("#panels");
const accountBtn = $("#account-btn"), menu = $("#account-menu");
panelBtn.onclick = () => toggle(panelBtn, panels);
accountBtn.onclick = (e) => { e.stopPropagation(); toggle(accountBtn, menu); };
document.addEventListener("click", (e) => {
  if (!menu.hidden && !menu.contains(e.target)) toggle(accountBtn, menu);
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!menu.hidden) toggle(accountBtn, menu);
  else if (!panels.hidden) toggle(panelBtn, panels);
});

const input = $("#input"), thread = $("#thread");
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = input.scrollHeight + "px";
});
input.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.shiftKey) return;
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  const m = document.createElement("div");
  m.className = "msg me";
  m.textContent = text;
  thread.append(m);
  thread.scrollTop = thread.scrollHeight;
  input.value = "";
  input.style.height = "auto";
});
$("#composer").addEventListener("submit", (e) => e.preventDefault());
