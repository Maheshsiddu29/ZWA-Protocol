# ZWA website (Next.js standalone). Build context: the repository root.
# Talks to the engine only through the private portal API; it mounts no
# ledger, evidence or wallet volume.
FROM node:22.21.0-bookworm-slim AS build
WORKDIR /repo/website
COPY website/package.json website/package-lock.json ./
RUN npm ci
COPY exchange/catalog.json /repo/exchange/catalog.json
COPY website/ ./
ENV NEXT_TELEMETRY_DISABLED=1 ZWA_STANDALONE=1
RUN npm run build

FROM node:22.21.0-bookworm-slim
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /app
COPY --from=build --chown=10001:10001 /repo/website/.next/standalone ./
COPY --from=build --chown=10001:10001 /repo/website/.next/static ./.next/static
COPY --from=build --chown=10001:10001 /repo/website/public ./public
USER 10001:10001
EXPOSE 3000
CMD ["node", "server.js"]
