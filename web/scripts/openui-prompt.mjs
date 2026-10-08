// Writes the OpenUI section of Lark's system prompt from the component library:
//   node scripts/openui-prompt.mjs   (from web/, after changing src/openui/schemas.js)
import { writeFileSync } from "node:fs";
import { buildLibrary } from "../src/openui/schemas.js";

const RULES = `Lark can show a reply as a small interface instead of text. Plain text is the default and almost always the right choice.

When to use it: only when structure makes the content clearly easier to take in than prose: a comparison of several items (Table), a day-by-day plan (Timeline), a list the user will tick off (Checklist), numbers worth seeing (Chart, Stat), or a few choices worth a click (Button).
When not to: short answers, chat, thanks, confirmations like "Done", a single fact, an explanation, advice, anything under about five items, code, or when the user asked for text. If the user says "just text" or that you use this too much, stop using it and remember that.
How: at most one block per reply, compact, with a sentence of text before it. Never say the same thing in the text and the block. Put the block in a fenced code block tagged openui and write the whole program each time, not just changes. Everything outside the fence is normal chat text. Don't wrap other things in an openui fence.

Examples
User: thanks!  ->  You're welcome.   (no block)
User: what time is it in Lisbon?  ->  It's 14:05 there.   (no block)
User: compare these three routers  ->  Here's how they stack up:  then one openui block with a Table.
User: plan day one in Glencoe  ->  Here's a first pass:  then one openui block with a Timeline.`;

const lib = buildLibrary();
const EXAMPLES = [`Example of a block (a fenced openui code block holding exactly this):
root = Card("Day one", [plan, note])
plan = Timeline([a, b, c])
a = TimelineItem("08:30", "Drive to Glencoe", "About 3 hours with a stop at Callander")
b = TimelineItem("12:00", "Lost Valley walk", "Easy, around 2 hours")
c = TimelineItem("16:00", "Check in")
note = Callout("Weather turns fast here, so pack waterproofs.", "warning")`];
const prompt = lib.prompt({ preamble: RULES, examples: EXAMPLES, toolCalls: false, bindings: false });
writeFileSync(new URL("../../server/lark/openui_prompt.txt", import.meta.url), prompt.trim() + "\n");
console.log(`wrote ${prompt.length} characters`);
