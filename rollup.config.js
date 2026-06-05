import { readFileSync }   from "fs";
import commonjs           from "@rollup/plugin-commonjs";
import json               from "@rollup/plugin-json";
import { nodeResolve }    from "@rollup/plugin-node-resolve";
import replace            from "@rollup/plugin-replace";
import typescript         from "@rollup/plugin-typescript";
import del                from "rollup-plugin-delete";
import externalGlobals    from "rollup-plugin-external-globals";

const manifest = JSON.parse(readFileSync("./plugin.json", "utf-8"));

// externalGlobals rewrites imports directly in the code at build time:
//   import React from 'react'        →  const React = SP_REACT
//   import { X } from '@decky/ui'   →  const { X } = DFL
//   import m from '@decky/manifest' →  const m = {"name":"Wake on Controller",...}
//
// This means NO module names leak into the IIFE parameter list — the
// output is a completely self-contained (function(){ ... })() with no
// external references that could be undefined at runtime.

export default {
  input: "./src/index.tsx",

  // Tell rollup not to bundle these — externalGlobals handles the replacement
  external: ["react", "react/jsx-runtime", "react-dom", "@decky/ui", "@decky/manifest"],

  plugins: [
    del({ targets: "./dist/*", force: true }),
    typescript({ tsconfig: "./tsconfig.json" }),
    json(),
    commonjs(),
    nodeResolve({ browser: true }),
    externalGlobals({
      "react":             "SP_REACT",
      "react/jsx-runtime": "SP_JSX",
      "react-dom":         "SP_REACTDOM",
      "@decky/ui":         "DFL",
      "@decky/manifest":   JSON.stringify(manifest),  // inlined at build time
    }),
    replace({
      preventAssignment: true,
      "process.env.NODE_ENV": JSON.stringify("production"),
    }),
  ],

  context: "window",

  output: {
    dir: "dist",
    format: "iife",
    // No `name` — without a name rollup emits a bare IIFE expression:
    //   (function () { ... return factory; }())
    // instead of a statement:
    //   var X = (function () { ... return factory; }())
    //
    // Decky loads plugins with eval(code). eval() returns the value of the
    // last expression. A bare IIFE is an expression so eval returns whatever
    // the IIFE returns (the plugin factory function). A var-statement is NOT
    // an expression so eval would return undefined.
    sourcemap: true,
  },
};
