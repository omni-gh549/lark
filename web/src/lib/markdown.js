import { marked } from "marked";
import DOMPurify from "dompurify";

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

export function render(text) {
  const html = DOMPurify.sanitize(marked.parse(text, { breaks: true, async: false }));
  // code blocks get a small header with the language and a copy button (the click is handled in Chat)
  return html.replace(/<pre><code(?: class="language-([\w+#.-]+)")?>/g, (_m, lang) =>
    `<div class="code"><div class="code-bar"><span>${lang ?? ""}</span><button type="button" class="code-copy">Copy</button></div><pre><code>`).replace(/<\/code><\/pre>/g, "</code></pre></div>");
}

// Splits a reply into prose and ```openui blocks. A block still being written has no closing fence yet: open is true.
export function segments(text) {
  const out = [];
  const re = /^```openui(?:-lang)?[ \t]*\r?\n/m;
  let rest = text;
  for (;;) {
    const m = re.exec(rest);
    if (!m) break;
    if (m.index > 0) out.push({ type: "md", text: rest.slice(0, m.index) });
    const body = rest.slice(m.index + m[0].length);
    const end = /^```[ \t]*$/m.exec(body);
    if (!end) {
      out.push({ type: "ui", text: body, open: true });
      return out;
    }
    out.push({ type: "ui", text: body.slice(0, end.index), open: false });
    rest = body.slice(end.index + end[0].length).replace(/^\r?\n/, "");
  }
  if (rest.trim() || !out.length) out.push({ type: "md", text: rest });
  return out;
}
