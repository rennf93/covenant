/**
 * Architecture enforcement for the TypeScript workspace (run via
 * `pnpm exec depcruise` in the Makefile's ts-arch target). The rule set is
 * the TS mirror of vouch's import-linter layers: sdk is the foundation,
 * indexer composes it, web composes both; nothing may skip a layer, and
 * source must never import built dist output.
 */
/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
  forbidden: [
    {
      name: "sdk-must-not-import-apps",
      comment: "sdk is the foundation package; it may not know about consumers",
      severity: "error",
      from: { path: "^sdk/src" },
      to: { path: "^(indexer|web)" },
    },
    {
      name: "indexer-must-not-import-web",
      comment: "indexer composes sdk only; web is a downstream consumer",
      severity: "error",
      from: { path: "^indexer/src" },
      to: { path: "^web" },
    },
    {
      name: "no-circular",
      severity: "error",
      from: {},
      to: { circular: true },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
  },
};
