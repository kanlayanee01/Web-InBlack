import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [
  { ignores: [".next/**", "next-env.d.ts"] },
  ...nextVitals,
  ...nextTs,
  // Loading data into state from an effect is how these client pages fetch after hydration.
  { rules: { "react-hooks/set-state-in-effect": "off" } },
];

export default config;
