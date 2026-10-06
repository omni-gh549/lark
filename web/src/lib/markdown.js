import { marked } from "marked";
import DOMPurify from "dompurify";

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

export function render(text) {
  return DOMPurify.sanitize(marked.parse(text, { breaks: true, async: false }));
}
