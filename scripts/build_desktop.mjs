#!/usr/bin/env node
/** Tiny zero-dependency ESM bundler for the Shortrizz Studio pane. */

import {
  existsSync,
  mkdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const DEFAULT_ROOT = path.resolve(HERE, "..");
export const ENTRY_REL = path.join("desktop", "src", "ui", "index.js");
export const STYLES_REL = path.join("desktop", "src", "ui", "styles.css");
export const OUTPUT_REL = path.join("desktop", "plugin.js");

export const ALLOWED_EXTERNAL_IMPORTS = Object.freeze([
  "react",
  "react/jsx-runtime",
  "@hermes/plugin-sdk",
]);

const ALLOWED_EXTERNAL = new Set(ALLOWED_EXTERNAL_IMPORTS);
const IDENTIFIER = /^[A-Za-z_$][\w$]*$/;

function relativeTo(root, filename) {
  return path.relative(root, filename).split(path.sep).join("/");
}

function fail(message) {
  throw new Error(message);
}

function parseNamedList(text) {
  const cleaned = text.replace(/^\{/, "").replace(/\}$/, "").trim();
  if (!cleaned) return [];
  return cleaned
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const pieces = part.split(/\s+as\s+/);
      const imported = pieces[0].trim();
      const local = (pieces[1] || imported).trim();
      return { imported, local };
    });
}

function parseImportClause(clause) {
  const result = { defaultName: null, namespaceName: null, named: [] };
  let rest = String(clause || "").trim();
  if (!rest) return result;

  if (rest.startsWith("*")) {
    const match = rest.match(/^\*\s+as\s+([A-Za-z_$][\w$]*)$/);
    if (!match) fail(`Unsupported namespace import: ${rest}`);
    result.namespaceName = match[1];
    return result;
  }
  if (rest.startsWith("{")) {
    result.named = parseNamedList(rest);
    return result;
  }

  const comma = rest.indexOf(",");
  if (comma === -1) {
    result.defaultName = rest.trim();
    return result;
  }

  result.defaultName = rest.slice(0, comma).trim();
  rest = rest.slice(comma + 1).trim();
  if (rest.startsWith("*")) {
    const match = rest.match(/^\*\s+as\s+([A-Za-z_$][\w$]*)$/);
    if (!match) fail(`Unsupported namespace import: ${rest}`);
    result.namespaceName = match[1];
  } else if (rest.startsWith("{")) {
    result.named = parseNamedList(rest);
  } else if (rest) {
    fail(`Unsupported import clause: ${clause}`);
  }
  return result;
}

function parseExportList(text) {
  const cleaned = text.trim();
  if (!cleaned) return [];
  return cleaned
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const pieces = part.split(/\s+as\s+/);
      const local = pieces[0].trim();
      const exported = (pieces[1] || local).trim();
      return { local, exported };
    });
}

function transformExportLine(line, moduleInfo) {
  const leading = (line.match(/^\s*/) || [""])[0];
  const text = line.slice(leading.length);
  let match;

  match = text.match(/^export\s+default\s+function\s+([A-Za-z_$][\w$]*)\b/);
  if (match) {
    moduleInfo.localExports.set("default", match[1]);
    return leading + text.replace(/^export\s+default\s+/, "");
  }

  match = text.match(/^export\s+default\s+class\s+([A-Za-z_$][\w$]*)\b/);
  if (match) {
    moduleInfo.localExports.set("default", match[1]);
    return leading + text.replace(/^export\s+default\s+/, "");
  }

  if (/^export\s+default\s+/.test(text)) {
    moduleInfo.localExports.set("default", "__default");
    return leading + text.replace(/^export\s+default\s+/, "const __default = ");
  }

  match = text.match(/^export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\b/);
  if (match) {
    moduleInfo.localExports.set(match[1], match[1]);
    return leading + text.replace(/^export\s+/, "");
  }

  match = text.match(/^export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)\b/);
  if (match) {
    moduleInfo.localExports.set(match[1], match[1]);
    return leading + text.replace(/^export\s+/, "");
  }

  match = text.match(/^export\s+class\s+([A-Za-z_$][\w$]*)\b/);
  if (match) {
    moduleInfo.localExports.set(match[1], match[1]);
    return leading + text.replace(/^export\s+/, "");
  }

  return line;
}

function splitModuleSource(source, moduleInfo) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const kept = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (/^import\s+/.test(trimmed) && !/^import\s*\(/.test(trimmed)) {
      let statement = line;
      let end = index;

      if (!/^import\s*["'][^"']+["']\s*;?$/.test(trimmed)) {
        while (
          !/from\s*["'][^"']+["']\s*;?\s*$/.test(statement.trim()) &&
          end + 1 < lines.length
        ) {
          end += 1;
          statement += "\n" + lines[end];
        }
      }

      const sideEffect = statement.trim().match(/^import\s*["']([^"']+)["']\s*;?$/);
      if (sideEffect) {
        moduleInfo.imports.push({
          specifier: sideEffect[1],
          defaultName: null,
          namespaceName: null,
          named: [],
        });
      } else {
        const match = statement
          .trim()
          .match(/^import\s+([\s\S]+?)\s+from\s*["']([^"']+)["']\s*;?$/);
        if (!match) fail(`Unsupported import statement in ${moduleInfo.display}`);
        const clause = parseImportClause(match[1]);
        moduleInfo.imports.push({
          specifier: match[2],
          defaultName: clause.defaultName,
          namespaceName: clause.namespaceName,
          named: clause.named,
        });
      }

      index = end + 1;
      continue;
    }

    if (/^export\s*\{/.test(trimmed)) {
      let statement = line;
      let end = index;
      while (
        !/\}\s*(?:from\s*["'][^"']+["'])?\s*;?\s*$/.test(statement.trim()) &&
        end + 1 < lines.length
      ) {
        end += 1;
        statement += "\n" + lines[end];
      }
      const match = statement
        .trim()
        .match(/^export\s*\{([\s\S]*?)\}\s*(?:from\s*["']([^"']+)["'])?\s*;?$/);
      if (!match) fail(`Unsupported export statement in ${moduleInfo.display}`);
      const entries = parseExportList(match[1]);
      if (match[2]) {
        entries.forEach((entry) => {
          moduleInfo.reexports.push({
            specifier: match[2],
            imported: entry.local,
            exported: entry.exported,
          });
        });
      } else {
        entries.forEach((entry) => {
          moduleInfo.localExports.set(entry.exported, entry.local);
        });
      }
      index = end + 1;
      continue;
    }

    if (/^export\s+\*/.test(trimmed)) {
      let statement = line;
      let end = index;
      while (
        !/from\s*["'][^"']+["']\s*;?\s*$/.test(statement.trim()) &&
        end + 1 < lines.length
      ) {
        end += 1;
        statement += "\n" + lines[end];
      }
      const match = statement.trim().match(/^export\s+\*\s+from\s*["']([^"']+)["']\s*;?$/);
      if (!match) fail(`Unsupported export * statement in ${moduleInfo.display}`);
      moduleInfo.starReexports.push(match[1]);
      index = end + 1;
      continue;
    }

    kept.push(transformExportLine(line, moduleInfo));
    index += 1;
  }

  moduleInfo.body = kept.join("\n");
}

function resolveLocal(root, fromFile, specifier) {
  const base = path.resolve(path.dirname(fromFile), specifier);
  const candidates = [base, `${base}.js`, path.join(base, "index.js")];
  for (const candidate of candidates) {
    if (existsSync(candidate) && statSync(candidate).isFile()) {
      return candidate;
    }
  }
  fail(
    `Cannot resolve ${JSON.stringify(specifier)} from ${relativeTo(root, fromFile)}`
  );
}

function parseModule(root, file) {
  const absolute = path.resolve(file);
  const moduleInfo = {
    file: absolute,
    display: relativeTo(root, absolute),
    imports: [],
    localExports: new Map(),
    reexports: [],
    starReexports: [],
    body: "",
    deps: [],
  };
  if (absolute.endsWith(".json")) {
    // ESM JSON modules expose the parsed value as the default export.
    // Do not treat the JSON text as a JS module body (a bare `{...}` is a block).
    const raw = readFileSync(absolute, "utf8");
    JSON.parse(raw); // fail fast on invalid JSON
    moduleInfo.body = `const __json = ${raw.trim()};`;
    moduleInfo.localExports.set("default", "__json");
    moduleInfo.isJson = true;
    return moduleInfo;
  }
  splitModuleSource(readFileSync(absolute, "utf8"), moduleInfo);
  return moduleInfo;
}

function collectModules(root, entryFile) {
  const cache = new Map();
  const order = [];
  const visiting = new Set();
  const done = new Set();

  function visit(file) {
    const absolute = path.resolve(file);
    if (done.has(absolute)) return cache.get(absolute);
    if (visiting.has(absolute)) {
      fail(`Circular import detected at ${relativeTo(root, absolute)}`);
    }
    visiting.add(absolute);

    const moduleInfo = parseModule(root, absolute);
    cache.set(absolute, moduleInfo);

    for (const item of moduleInfo.imports) {
      if (item.specifier.startsWith(".")) {
        const dep = visit(resolveLocal(root, absolute, item.specifier));
        item.dep = dep;
        moduleInfo.deps.push(dep);
      } else {
        if (!ALLOWED_EXTERNAL.has(item.specifier)) {
          fail(
            `Unsupported import ${JSON.stringify(item.specifier)} in ${moduleInfo.display}`
          );
        }
        item.external = true;
      }
    }

    for (const item of moduleInfo.reexports) {
      if (!item.specifier.startsWith(".")) {
        fail(
          `Only relative re-exports are supported in ${moduleInfo.display}`
        );
      }
      const dep = visit(resolveLocal(root, absolute, item.specifier));
      item.dep = dep;
      moduleInfo.deps.push(dep);
    }

    for (const specifier of moduleInfo.starReexports) {
      if (!specifier.startsWith(".")) {
        fail(`Only relative export * is supported in ${moduleInfo.display}`);
      }
      const dep = visit(resolveLocal(root, absolute, specifier));
      moduleInfo.starDeps = moduleInfo.starDeps || [];
      moduleInfo.starDeps.push(dep);
      moduleInfo.deps.push(dep);
    }

    visiting.delete(absolute);
    done.add(absolute);
    order.push(moduleInfo);
    return moduleInfo;
  }

  const entry = visit(entryFile);
  return { order, entry };
}

function exportNames(moduleInfo, seen = new Set()) {
  if (seen.has(moduleInfo.file)) return new Set();
  seen.add(moduleInfo.file);
  const names = new Set(moduleInfo.localExports.keys());
  for (const item of moduleInfo.reexports) names.add(item.exported);
  for (const dep of moduleInfo.starDeps || []) {
    for (const name of exportNames(dep, seen)) {
      if (name !== "default") names.add(name);
    }
  }
  return names;
}

function createExternalContext(format = "esm") {
  const bindings = new Map();
  const imports = [];
  let counter = 0;

  function binding(specifier, key, imported) {
    const mapKey = `${specifier}\u0000${key}`;
    if (bindings.has(mapKey)) return bindings.get(mapKey);
    const identifier = `__ext${counter}`;
    counter += 1;
    bindings.set(mapKey, identifier);
    if (format === "iife") {
      const kind = key === "default" || key === "namespace" ? key : "named";
      imports.push(
        `const ${identifier} = __hgExternal(${JSON.stringify(specifier)}, ${JSON.stringify(kind)}, ${JSON.stringify(imported || null)});`
      );
    } else if (key === "default") {
      imports.push(`import ${identifier} from ${JSON.stringify(specifier)};`);
    } else if (key === "namespace") {
      imports.push(`import * as ${identifier} from ${JSON.stringify(specifier)};`);
    } else {
      imports.push(
        `import { ${imported} as ${identifier} } from ${JSON.stringify(specifier)};`
      );
    }
    return identifier;
  }

  return {
    imports,
    aliasFor(specifier, kind, imported) {
      if (kind === "default") return binding(specifier, "default");
      if (kind === "namespace") return binding(specifier, "namespace");
      return binding(specifier, `named:${imported}`, imported);
    },
  };
}

function moduleReturn(moduleInfo, idFor) {
  const entries = [];
  const seen = new Set();

  for (const [exported, local] of moduleInfo.localExports) {
    entries.push(`${JSON.stringify(exported)}: ${local}`);
    seen.add(exported);
  }

  for (const item of moduleInfo.reexports) {
    if (seen.has(item.exported)) continue;
    const depVar = `__m${idFor(item.dep)}`;
    entries.push(`${JSON.stringify(item.exported)}: ${depVar}[${JSON.stringify(item.imported)}]`);
    seen.add(item.exported);
  }

  for (const dep of moduleInfo.starDeps || []) {
    const depVar = `__m${idFor(dep)}`;
    for (const name of exportNames(dep)) {
      if (name === "default" || seen.has(name)) continue;
      entries.push(`${JSON.stringify(name)}: ${depVar}[${JSON.stringify(name)}]`);
      seen.add(name);
    }
  }

  return entries;
}

function generateModule(moduleInfo, idFor, external) {
  const id = idFor(moduleInfo);
  const lines = [`const __m${id} = (function () {`];

  for (const item of moduleInfo.imports) {
    if (item.external) {
      if (item.defaultName) {
        lines.push(
          `  const ${item.defaultName} = ${external.aliasFor(
            item.specifier,
            "default"
          )};`
        );
      }
      if (item.namespaceName) {
        lines.push(
          `  const ${item.namespaceName} = ${external.aliasFor(
            item.specifier,
            "namespace"
          )};`
        );
      }
      for (const named of item.named) {
        if (!IDENTIFIER.test(named.local)) {
          fail(`Invalid local import name ${JSON.stringify(named.local)}`);
        }
        lines.push(
          `  const ${named.local} = ${external.aliasFor(
            item.specifier,
            "named",
            named.imported
          )};`
        );
      }
    } else {
      const depVar = `__m${idFor(item.dep)}`;
      if (item.defaultName) {
        lines.push(`  const ${item.defaultName} = ${depVar}["default"];`);
      }
      if (item.namespaceName) {
        lines.push(`  const ${item.namespaceName} = ${depVar};`);
      }
      for (const named of item.named) {
        lines.push(
          `  const ${named.local} = ${depVar}[${JSON.stringify(named.imported)}];`
        );
      }
    }
  }

  lines.push(moduleInfo.body);
  const entries = moduleReturn(moduleInfo, idFor);
  lines.push(`  return { ${entries.join(", ")} };`);
  lines.push("})();");
  return lines.join("\n");
}

function styleBundle(css) {
  return css.replace(/\r\n/g, "\n");
}

/** Build the complete plugin.js text in memory. */
export function buildBundle(options = {}) {
  const root = path.resolve(options.root || DEFAULT_ROOT);
  const entryFile = path.resolve(root, options.entry || ENTRY_REL);
  const stylesFile = path.resolve(root, options.styles || STYLES_REL);

  if (!existsSync(stylesFile)) {
    fail(`Missing stylesheet ${relativeTo(root, stylesFile)}`);
  }

  const { order, entry } = collectModules(root, entryFile);
  const ids = new Map(order.map((moduleInfo, index) => [moduleInfo.file, index]));
  const idFor = (moduleInfo) => ids.get(moduleInfo.file);
  const external = createExternalContext(options.format || "esm");

  const moduleText = order
    .map((moduleInfo) => {
      const header = `// ${moduleInfo.display}`;
      return `${header}\n${generateModule(moduleInfo, idFor, external)}`;
    })
    .join("\n\n");

  const css = styleBundle(readFileSync(stylesFile, "utf8"));
  const entryVar = `__m${idFor(entry)}`;
  const entryNames = [...exportNames(entry)]
    .filter((name) => name !== "default")
    .filter((name) => name !== "register")
    .filter((name) => name !== "injectShortrizzStyles")
    .sort();

  const namedExports = entryNames
    .map((name) => `export const ${name} = ${entryVar}[${JSON.stringify(name)}];`)
    .join("\n");

  return [
    "// Generated by scripts/build_desktop.mjs. Do not edit by hand.",
    "// Rebuild with: node scripts/build_desktop.mjs",
    "",
    ...external.imports,
    "",
    moduleText,
    "",
    `const SHORTRIZZ_STYLE_ID = ${JSON.stringify("shortrizz-desktop-styles")};`,
    `const SHORTRIZZ_STYLES = ${JSON.stringify(css)};`,
    "",
    "function injectShortrizzStyles() {",
    "  if (typeof document === \"undefined\") return false;",
    "  const existing = document.getElementById(SHORTRIZZ_STYLE_ID);",
    "  if (existing) return false;",
    "  const style = document.createElement(\"style\");",
    "  style.id = SHORTRIZZ_STYLE_ID;",
    "  style.textContent = SHORTRIZZ_STYLES;",
    "  const parent = document.head || document.documentElement;",
    "  if (!parent) return false;",
    "  parent.appendChild(style);",
    "  return true;",
    "}",
    "",
    `const __srCreatePlugin = ${entryVar}[${JSON.stringify("createDesktopPlugin")}];`,
    `const __srRegisterPane = ${entryVar}[${JSON.stringify("registerShortrizzPane")}];`,
    "const __srPlugin = typeof __srCreatePlugin === \"function\"",
    "  ? __srCreatePlugin()",
    "  : { id: \"shortrizz\", name: \"Shortrizz Studio\", defaultEnabled: true, register: __srRegisterPane };",
    "const __srPluginRegister =",
    "  typeof __srRegisterPane === \"function\"",
    "    ? __srRegisterPane",
    "    : typeof __srPlugin.register === \"function\"",
    "      ? __srPlugin.register",
    "      : null;",
    "",
    "export function register(ctx) {",
    "  injectShortrizzStyles();",
    "  return __srPluginRegister ? __srPluginRegister(ctx) : false;",
    "}",
    "",
    "__srPlugin.register = register;",
    "",
    namedExports,
    "",
    "export { injectShortrizzStyles as injectStyles };",
    "export default __srPlugin;",
    "",
  ].join("\n");
}

function parseArgs(argv) {
  const options = { check: false, root: DEFAULT_ROOT };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--check") {
      options.check = true;
    } else if (arg === "--root") {
      index += 1;
      if (!argv[index]) fail("--root needs a directory");
      options.root = path.resolve(argv[index]);
    } else if (arg === "--help" || arg === "-h") {
      options.help = true;
    } else {
      fail(`Unknown argument ${JSON.stringify(arg)}`);
    }
  }
  return options;
}

function main(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);
  if (options.help) {
    console.log("usage: node scripts/build_desktop.mjs [--check] [--root DIR]");
    return;
  }

  const root = path.resolve(options.root);
  const outputFile = path.resolve(root, OUTPUT_REL);
  const output = buildBundle({ root });
  const display = relativeTo(root, outputFile);

  if (options.check) {
    if (!existsSync(outputFile)) {
      fail(`${display} is missing; run node scripts/build_desktop.mjs`);
    }
    const current = readFileSync(outputFile, "utf8");
    if (current !== output) {
      fail(`${display} is stale; run node scripts/build_desktop.mjs`);
    }
    console.log(`${display} is up to date`);
    return;
  }

  mkdirSync(path.dirname(outputFile), { recursive: true });
  writeFileSync(outputFile, output, "utf8");
  console.log(`wrote ${display}`);
}

const invokedDirectly =
  process.argv[1] &&
  path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  try {
    main();
  } catch (error) {
    console.error(`desktop build failed: ${error.message}`);
    process.exitCode = 1;
  }
}
