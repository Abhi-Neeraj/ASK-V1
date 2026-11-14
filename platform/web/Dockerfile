# ASK platform web app (Next.js).
#
# NOT BUILT OR RUN where this was written: there was no Docker on that machine. `npm run build`
# itself was run there and passes; this file wraps it. See platform/docs/RUNBOOK.md.
#
#   docker build -t ask-web platform/web

FROM node:24-slim AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# `next start` serves ./public, so the folder has to exist even when the project has no static files.
RUN mkdir -p public

# Both of these are fixed when the app is BUILT, not when it starts:
#  - API_ORIGIN is where Next.js forwards /api/* if a request ever reaches it. In the Compose stack the
#    reverse proxy sends /api straight to the API, so this is only a fallback.
#  - NEXT_PUBLIC_DEMO_MODE must stay false. True swaps the real API for invented sample data.
ARG API_ORIGIN=http://api:8000
ARG NEXT_PUBLIC_DEMO_MODE=false
ENV API_ORIGIN=$API_ORIGIN \
    NEXT_PUBLIC_DEMO_MODE=$NEXT_PUBLIC_DEMO_MODE \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

# The whole dependency folder is reused rather than pruned: Next.js reads next.config.ts when it starts,
# and loading a TypeScript config can need `typescript`, which is a development dependency. A smaller
# image is possible once a start has been tried with `npm ci --omit=dev`.
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/next.config.ts ./next.config.ts

USER node
EXPOSE 3000
CMD ["node_modules/.bin/next", "start", "--hostname", "0.0.0.0", "--port", "3000"]
