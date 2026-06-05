// Use IIFE format — no `import`, no `export`, no `System.register`.
// Works in every JS execution context Decky might use (eval, script tag,
// dynamic import, new Function — doesn't matter).
//
// `definePlugin` from @decky/api self-registers the plugin via
//   window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit
// when it runs, so Decky doesn't need to read a module export at all.
//
// React, ReactDOM, and @decky/ui are provided by Decky's runtime as globals:
//   SP_REACT, SP_REACTDOM, SP_JSX, DFL
// so we declare them external and map them in output.globals.
// @decky/api gets bundled in (it's a thin wrapper around window.__DECKY_SECRET_INTERNALS).

import { readFileSync }  from "fs";
import commonjs          from "@rollup/plugin-commonjs";
import json              from "@rollup/plugin-json";
import { nodeResolve }   from "@rollup/plugin-node-resolve";
import replace           from "@rollup/plugin-replace";
import typescript        from "@rollup/plugin-typescript";
import del               from "rollup-plugin-delete";

const manifest = JSON.parse(readFileSync("./plugin.json", "utf-8"));

export default {
  input: "./src/index.tsx",

  external: ["react", "react/jsx-runtime", "react-dom", "@decky/ui"],

  plugins: [
    del({ targets: "./dist/*", force: true }),
    typescript({ tsconfig: "./tsconfig.json" }),
    json(),
    commonjs(),
    nodeResolve({ browser: true }),
    replace({
      preventAssignment: true,
      "process.env.NODE_ENV": JSON.stringify("production"),
    }),
  ],

  context: "window",

  output: {
    dir: "dist",
    format: "iife",
    name: "DeckPlugin",
    globals: {
      "react":             "SP_REACT",
      "react/jsx-runtime": "SP_JSX",
      "react-dom":         "SP_REACTDOM",
      "@decky/ui":         "DFL",
    },
    sourcemap: true,
  },
};
