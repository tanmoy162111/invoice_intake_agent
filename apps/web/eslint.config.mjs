import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [
  ...nextVitals,
  ...nextTs,
  { ignores: [".next/**", "node_modules/**", "src/lib/api/schema.d.ts", "next-env.d.ts"] },
  { rules: { "@typescript-eslint/no-explicit-any": "error" } },
];
export default config;
