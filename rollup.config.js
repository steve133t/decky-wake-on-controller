// Decky Loader 3.x uses SystemJS as its module loader.
// Output must be `format: 'system'` — not native ESM — otherwise Decky
// tries to evaluate the file as a plain script and chokes on `export`.
import resolve  from "@rollup/plugin-node-resolve";
import commonjs from "@rollup/plugin-commonjs";
import typescript from "@rollup/plugin-typescript";
import replace from "@rollup/plugin-replace";

export default {
  input: "src/index.tsx",
  output: {
    dir: "dist",
    format: "system",   // SystemJS — what Decky 3.x loads via System.import()
    sourcemap: true,
    exports: "default",
  },
  // These are provided by Decky's runtime; do not bundle them
  external: ["react", "react-dom", "@decky/ui", "@decky/api"],
  plugins: [
    resolve({ browser: true }),
    commonjs(),
    typescript({
      tsconfig: "./tsconfig.json",
      declaration: true,
      declarationDir: "./dist",
    }),
    replace({
      "process.env.NODE_ENV": JSON.stringify("production"),
      preventAssignment: true,
    }),
  ],
};
