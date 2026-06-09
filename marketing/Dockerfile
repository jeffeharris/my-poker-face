# ── Build stage ──────────────────────────────────────────────────────────
FROM node:22-alpine AS builder
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Build Astro directly (NOT `npm run build`, which first runs sync-opponents.mjs
# — that reads the game's poker/personalities.json, which isn't in this build
# context). The build consumes the COMMITTED src/data/opponents.json and
# public/avatars/. Regenerate those locally with `npm run avatars && npm run
# build`, then commit, when the roster changes.
RUN npx astro build

# ── Serve stage ──────────────────────────────────────────────────────────
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
