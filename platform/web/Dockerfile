# ASK web frontend: the ASK app (Expo Router), exported as a static web build and served on port 3000.
#
# NOT BUILT OR RUN with Docker where this was written (no Docker on that machine). `npm run build:web`
# and `node serve.mjs` were run there directly. See platform/docs/RUNBOOK.md.
#
#   docker build -t ask-web platform/web

FROM node:24-slim AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
ENV EXPO_NO_TELEMETRY=1
RUN npm run build:web

FROM node:24-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOST=0.0.0.0

# Only the exported files and the tiny dependency-free server are shipped.
COPY --from=build /app/dist ./dist
COPY --from=build /app/serve.mjs ./serve.mjs

USER node
EXPOSE 3000
CMD ["node", "serve.mjs"]
