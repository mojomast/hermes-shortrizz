import { register } from "node:module";

register(new URL("./mock-loader.mjs", import.meta.url), import.meta.url);
