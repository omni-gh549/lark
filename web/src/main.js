import "./app.css";
import { mount } from "svelte";
import App from "./App.svelte";
import { getBackdrop } from "./lib/backdrop.js";

document.documentElement.dataset.backdrop = getBackdrop();

mount(App, { target: document.getElementById("app") });
