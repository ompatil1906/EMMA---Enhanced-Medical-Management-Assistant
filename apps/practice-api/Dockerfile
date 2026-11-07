# EMMA practice-api — NestJS 11 production image (TypeORM over MySQL/SQLite).
# Build context must be the REPOSITORY ROOT (context: ../.. in docker-compose.yml).

FROM node:22-bookworm-slim AS build

WORKDIR /app
RUN corepack enable
COPY apps/practice-api/package.json apps/practice-api/pnpm-lock.yaml apps/practice-api/pnpm-workspace.yaml ./apps/practice-api/
WORKDIR /app/apps/practice-api
RUN pnpm install --frozen-lockfile
COPY apps/practice-api/ ./
RUN pnpm build

FROM node:22-bookworm-slim AS production

WORKDIR /app/apps/practice-api
RUN corepack enable
COPY apps/practice-api/package.json apps/practice-api/pnpm-lock.yaml apps/practice-api/pnpm-workspace.yaml ./apps/practice-api/
RUN pnpm install --frozen-lockfile --prod
COPY --from=build /app/apps/practice-api/dist ./dist
COPY apps/practice-api/migrations ./migrations
ENV NODE_ENV=production
EXPOSE 3000
CMD ["node", "dist/main.js"]
