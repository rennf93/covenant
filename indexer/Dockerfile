# pnpm workspace monorepo: build context is the repo root.
FROM node:24-alpine
WORKDIR /app

COPY package.json pnpm-workspace.yaml ./
COPY sdk/package.json sdk/
COPY indexer/package.json indexer/
RUN corepack enable && pnpm install --filter @proven/indexer --no-frozen-lockfile

COPY sdk/ sdk/
COPY indexer/ indexer/
RUN pnpm --filter @proven/sdk run build

EXPOSE 8787
# node 24 runs TypeScript directly (type stripping)
CMD ["node", "indexer/src/main.ts"]
