FROM node:24-bookworm-slim AS base
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
EXPOSE 3000

FROM base AS development
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY app ./app
COPY public ./public
COPY next.config.mjs world-map.svg ./
ENV JIMIX_API_URL=http://host.docker.internal:8000
CMD ["node", "node_modules/next/dist/bin/next", "dev", "--hostname", "0.0.0.0"]

FROM development AS build
ARG JIMIX_API_URL=http://host.docker.internal:8000
ENV JIMIX_API_URL=$JIMIX_API_URL
RUN JIMIX_STANDALONE=1 npm run build

FROM base AS production
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER node
CMD ["node", "server.js"]
