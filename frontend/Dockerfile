FROM node:24-bookworm-slim AS base
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
EXPOSE 3000

FROM base AS development
RUN npm ci --no-audit --no-fund
COPY app ./app
COPY public ./public
COPY next.config.mjs world-map.svg ./
ENV JIMIX_API_URL=http://host.docker.internal:8000
CMD ["node", "node_modules/next/dist/bin/next", "dev", "--hostname", "0.0.0.0"]

FROM development AS build
ARG JIMIX_API_URL=http://host.docker.internal:8000
ENV JIMIX_API_URL=$JIMIX_API_URL
RUN npm run build && rm -rf .next/cache

FROM base AS production
ENV NODE_ENV=production
RUN npm ci --omit=dev --no-audit --no-fund
COPY --from=build --chown=node:node /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/next.config.mjs ./next.config.mjs
ARG JIMIX_API_URL=http://host.docker.internal:8000
ENV JIMIX_API_URL=$JIMIX_API_URL
USER node
CMD ["node", "node_modules/next/dist/bin/next", "start", "--hostname", "0.0.0.0"]
