/** The pane's only door to ctx.rest / ctx.os. Tests replace this. */

let current = null;

export function setHostTransport(transport) {
  current = transport || null;
}

export function getHostTransport() {
  return current;
}
