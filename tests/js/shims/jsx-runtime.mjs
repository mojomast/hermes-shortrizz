/** Tiny React stand-in for node:test. One fiber, hooks in call order. */

let fiber = null;

function ensure() {
  if (!fiber) fiber = { hooks: [], cursor: 0, effects: [], rendering: false };
  return fiber;
}

export function useState(init) {
  const current = ensure();
  const index = current.cursor;
  current.cursor += 1;
  if (current.hooks.length <= index) {
    current.hooks.push(typeof init === "function" ? init() : init);
  }
  const set = (value) => {
    const prev = current.hooks[index];
    current.hooks[index] = typeof value === "function" ? value(prev) : value;
  };
  return [current.hooks[index], set];
}

export function useRef(init) {
  const current = ensure();
  const index = current.cursor;
  current.cursor += 1;
  if (current.hooks.length <= index) current.hooks.push({ current: init });
  return current.hooks[index];
}

export function useEffect(fn) {
  ensure().effects.push(fn);
}

export function jsx(type, props, key) {
  const next = Object.assign({}, props || {});
  if (key !== undefined) next.key = key;
  if (typeof type === "function") {
    const current = ensure();
    const root = !current.rendering;
    if (root) {
      current.cursor = 0;
      current.effects = [];
      current.rendering = true;
    }
    try {
      return type(next);
    } finally {
      if (root) current.rendering = false;
    }
  }
  return { type, props: next };
}

export const jsxs = jsx;
export const Fragment = "fragment";

export function resetFiber() {
  fiber = null;
}

export function rerender(type, props) {
  return jsx(type, props);
}

export function flushEffects() {
  const current = ensure();
  const effects = current.effects.splice(0);
  const cleanups = [];
  for (const fn of effects) {
    const cleanup = fn();
    if (typeof cleanup === "function") cleanups.push(cleanup);
  }
  return cleanups;
}
