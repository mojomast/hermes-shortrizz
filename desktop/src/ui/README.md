# Shortrizz Studio sources

These files are the Desktop pane. They are not loaded directly: `desktop/plugin.js`
is the bundle Hermes copies. Rebuild it with `node scripts/build_desktop.mjs`.
`node scripts/build_desktop.mjs --check` fails when the committed bundle is stale.

Sources import only `react`, `react/jsx-runtime`, and (in the bundle footer)
`@hermes/plugin-sdk` is reserved for the host. UI is `jsx()` / `jsxs()` calls.
The pane reaches the Python half with `ctx.rest` (`dashboard/plugin_api.py`)
and reveals a run folder with `ctx.os.revealPath`.
