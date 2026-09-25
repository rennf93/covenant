# pnpm workspace monorepo: build context is the repo root.
FROM node:24-alpine
WORKDIR /app

COPY package.json pnpm-workspace.yaml ./
COPY sdk/package.json sdk/
COPY web/package.json web/
RUN corepack enable && pnpm install --filter @covenant/web --no-frozen-lockfile

COPY sdk/ sdk/
COPY web/ web/
RUN pnpm --filter @covenant/sdk run build && pnpm --filter @covenant/web run build

ENV NODE_ENV=production
ENV INDEXER_URL=http://indexer:8787
EXPOSE 3000
CMD ["pnpm", "--filter", "@covenant/web", "run", "start"]
