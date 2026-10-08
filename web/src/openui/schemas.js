// The components Lark's model may use in a reply, as OpenUI Lang. This file has no Svelte in it, so
// scripts/openui-prompt.mjs can build the prompt from it in plain Node. The web app passes the renderers in.
import { z } from "zod/v4";
import { createLibrary, defineComponent } from "@openuidev/lang-core";

export function buildLibrary(r = {}) {
  const def = (name, description, props) => defineComponent({ name, description, props, component: r[name] ?? null });

  const Text = def("Text", "A short piece of text. Use tone muted for secondary notes.", z.object({
    content: z.string(),
    tone: z.enum(["normal", "muted"]).optional(),
  }));
  const Stat = def("Stat", "One headline figure with a label, such as a temperature or a price.", z.object({
    label: z.string(),
    value: z.string(),
    note: z.string().optional(),
  }));
  const Table = def("Table", "A table comparing several items. Every row has one cell per column.", z.object({
    columns: z.array(z.string()),
    rows: z.array(z.array(z.union([z.string(), z.number()]))),
  }));
  const Chart = def("Chart", "A small bar or line chart of one series of numbers. Only for data that is easier to see than to read.", z.object({
    kind: z.enum(["bar", "line"]),
    labels: z.array(z.string()),
    values: z.array(z.number()),
    unit: z.string().optional(),
  }));
  const Checklist = def("Checklist", "Items the user can tick off, such as a packing list.", z.object({
    items: z.array(z.string()),
  }));
  const TimelineItem = def("TimelineItem", "One step of a timeline.", z.object({
    time: z.string().optional(),
    title: z.string(),
    detail: z.string().optional(),
  }));
  const Timeline = def("Timeline", "An ordered plan or itinerary, one TimelineItem per step.", z.object({
    items: z.array(TimelineItem.ref),
  }));
  const Callout = def("Callout", "A short highlighted note, such as a warning or a tip.", z.object({
    text: z.string(),
    tone: z.enum(["info", "warning"]).optional(),
  }));
  const Button = def("Button", "A choice the user can click. Clicking sends the message (or the label) back as the user's next message.", z.object({
    label: z.string(),
    message: z.string().optional(),
  }));
  const Link = def("Link", "A link to a web page (https only).", z.object({
    label: z.string(),
    url: z.string(),
  }));
  const Tags = def("Tags", "A few short labels side by side.", z.object({
    items: z.array(z.string()),
  }));

  const leaf = z.union([Text.ref, Stat.ref, Table.ref, Chart.ref, Checklist.ref, Timeline.ref, Callout.ref, Button.ref, Link.ref, Tags.ref]);
  const Stack = def("Stack", "Lays its children out in a column, or side by side with direction row (good for a few Stats).", z.object({
    children: z.array(leaf),
    direction: z.enum(["column", "row"]).optional(),
  }));
  const Card = def("Card", "The root: a titled card holding the content.", z.object({
    title: z.string().optional(),
    children: z.array(z.union([leaf, Stack.ref])),
  }));

  return createLibrary({
    components: [Card, Stack, Text, Stat, Table, Chart, Checklist, Timeline, TimelineItem, Callout, Button, Link, Tags],
    root: "Card",
  });
}
