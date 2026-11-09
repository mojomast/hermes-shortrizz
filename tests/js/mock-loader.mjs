const SHIMS = {
  react: "./shims/react.mjs",
  "react/jsx-runtime": "./shims/jsx-runtime.mjs",
  "@hermes/plugin-sdk": "./shims/sdk.mjs",
};

export async function resolve(specifier, context, nextResolve) {
  if (Object.prototype.hasOwnProperty.call(SHIMS, specifier)) {
    return {
      url: new URL(SHIMS[specifier], import.meta.url).href,
      shortCircuit: true,
    };
  }
  return nextResolve(specifier, context);
}
