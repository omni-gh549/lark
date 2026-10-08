import { buildLibrary } from "./schemas.js";
import Card from "./components/Card.svelte";
import Stack from "./components/Stack.svelte";
import Text from "./components/Text.svelte";
import Stat from "./components/Stat.svelte";
import Table from "./components/Table.svelte";
import Chart from "./components/Chart.svelte";
import Checklist from "./components/Checklist.svelte";
import Timeline from "./components/Timeline.svelte";
import TimelineItem from "./components/TimelineItem.svelte";
import Callout from "./components/Callout.svelte";
import Button from "./components/Button.svelte";
import Link from "./components/Link.svelte";
import Tags from "./components/Tags.svelte";

export const library = buildLibrary({ Card, Stack, Text, Stat, Table, Chart, Checklist, Timeline, TimelineItem, Callout, Button, Link, Tags });
