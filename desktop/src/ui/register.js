import { jsx } from "react/jsx-runtime";
import { setHostTransport } from "./host.js";
import { StudioPane } from "./pane.js";

export const PANE_ID = "studio";
export const PANE_TITLE = "Shortrizz Studio";

export function createPaneContribution() {
  return {
    id: PANE_ID,
    title: PANE_TITLE,
    area: "panes",
    order: 40,
    data: { placement: "right", width: "960px" },
    render: function renderStudio() {
      return jsx(StudioPane, {});
    },
  };
}

/**
 * Desktop entry. The pane talks only through ctx.rest (plugin routes) and
 * ctx.os.revealPath. Hermes >= 0.23.0.
 */
export function registerShortrizzPane(ctx) {
  if (!ctx || typeof ctx.register !== "function") return false;
  const rest = typeof ctx.rest === "function"
    ? function shortrizzRest(path, opts) { return ctx.rest(path, opts); }
    : null;
  const reveal = ctx.os && typeof ctx.os.revealPath === "function"
    ? function shortrizzReveal(path) { return ctx.os.revealPath(path); }
    : null;
  setHostTransport({
    rest,
    reveal,
    setInterval: typeof ctx.setInterval === "function" ? ctx.setInterval.bind(ctx) : null,
  });
  ctx.register(createPaneContribution());
  return true;
}

export function createDesktopPlugin() {
  return {
    id: "shortrizz",
    name: PANE_TITLE,
    defaultEnabled: true,
    register: registerShortrizzPane,
  };
}
