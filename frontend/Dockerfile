# syntax=docker/dockerfile:1

# --- Сборка ------------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

# Слой зависимостей отделен от исходников: правка кода не пересобирает npm ci.
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY tsconfig.json tsconfig.node.json vite.config.ts index.html ./
COPY src ./src

RUN npm run build

# --- Раздача -----------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.inc /etc/nginx/conf.d/security-headers.inc

# nginx в официальном образе уже умеет работать от непривилегированного
# пользователя внутри контейнера; порт 80 остается внутренним.
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
