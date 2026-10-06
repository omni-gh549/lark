// Components the model can place in the panel drawer.
// `schema` is JSON Schema for the props, so these can be handed to a model as tool definitions later.
import ActionCard from "./panels/ActionCard.svelte";
import TaskCard from "./panels/TaskCard.svelte";
import WeatherCard from "./panels/WeatherCard.svelte";
import BarCard from "./panels/BarCard.svelte";

const actions = {
  type: "array",
  items: {
    type: "object",
    properties: { id: { type: "string" }, label: { type: "string" }, primary: { type: "boolean" } },
    required: ["label"],
  },
};

export const registry = {
  action: {
    component: ActionCard,
    description: "A suggested action the user can approve, such as a drafted reply.",
    schema: {
      type: "object",
      properties: { verb: { type: "string" }, text: { type: "string" }, actions },
      required: ["text"],
    },
    example: {
      verb: "Reply",
      text: 'to Sam: "Friday at 7 works, see you at Rosa\'s"',
      actions: [{ label: "Send", primary: true }, { label: "Edit" }],
    },
  },
  task: {
    component: TaskCard,
    description: "Progress of a long-running task, such as a booking on the VM.",
    schema: {
      type: "object",
      properties: {
        title: { type: "string" },
        step: { type: "integer" },
        total: { type: "integer" },
        status: { type: "string" },
        live: { type: "boolean" },
        actions,
      },
      required: ["title", "step", "total"],
    },
    example: {
      title: "Booking a table at Rosa's",
      step: 4,
      total: 9,
      status: "choosing a time",
      actions: [{ label: "Watch" }, { label: "Take over" }, { label: "Stop" }],
    },
  },
  weather: {
    component: WeatherCard,
    description: "Current conditions and today's range for a place, in °C.",
    schema: {
      type: "object",
      properties: {
        place: { type: "string" },
        condition: { type: "string" },
        temp: { type: "number" },
        feels: { type: "number" },
        high: { type: "number" },
        low: { type: "number" },
      },
      required: ["condition", "temp", "high", "low"],
    },
    example: { place: "London", condition: "Overcast", temp: 12.4, feels: 10.7, high: 14.2, low: 6.6 },
  },
  bars: {
    component: BarCard,
    description: "A small bar chart for a handful of labelled values.",
    schema: {
      type: "object",
      properties: {
        title: { type: "string" },
        summary: { type: "string" },
        bars: {
          type: "array",
          items: {
            type: "object",
            properties: { label: { type: "string" }, value: { type: "number" } },
            required: ["label", "value"],
          },
        },
      },
      required: ["title", "bars"],
    },
    example: {
      title: "Runs this week",
      summary: "14.2 km",
      bars: [
        { label: "M", value: 5.1 }, { label: "T", value: 0 }, { label: "W", value: 7.4 },
        { label: "T", value: 0 }, { label: "F", value: 3.7 }, { label: "S", value: 0 }, { label: "S", value: 0 },
      ],
    },
  },
};
