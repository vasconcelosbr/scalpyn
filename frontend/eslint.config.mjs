import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  // TypeScript restrictions apply to TypeScript, not the existing CommonJS browser harnesses.
  ...nextTs.map(config => config.ignores ? config : {...config, files:["**/*.{ts,tsx,mts,cts}"]}),
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  {
    // Match Next's plugin scope: CommonJS utility files do not load React hooks.
    files: ["**/*.{js,jsx,mjs,ts,tsx,mts,cts}"],
    // Legacy debt remains visible in CI as warnings while new systemic AI
    // code is required to typecheck and build. No rule is disabled.
    rules: {
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/static-components": "warn",
      "react-hooks/exhaustive-deps": "warn",
      "react-hooks/immutability": "warn",
      "react-hooks/purity": "warn",
      "react-hooks/refs": "warn",
      "react/no-unescaped-entities": "warn",
      "@next/next/no-html-link-for-pages": "warn",
      "@next/next/no-img-element": "warn",
      "prefer-const": "warn",
    },
  },
  {
    files:["**/*.{ts,tsx,mts,cts}"],
    rules:{"@typescript-eslint/no-explicit-any":"warn", "@typescript-eslint/no-unused-vars":"warn"},
  },
]);

export default eslintConfig;
