import "./app.css";
import { mount } from "svelte";
import App from "./App.svelte";
import { getBackdrop, getPalette } from "./lib/backdrop.js";

document.documentElement.dataset.backdrop = getBackdrop();
document.documentElement.dataset.palette = getPalette();

mount(App, { target: document.getElementById("app") });
