# 阶段 1：Node 构建
FROM node:20-alpine AS builder
WORKDIR /app

COPY package*.json ./
RUN npm ci --no-audit --no-fund

COPY . .

# 注意：ARG 不要写默认值。Zeabur 会在构建前自动注入同名环境变量，
# 若这里带默认值，紧随其后的 ENV 会把注入值覆盖回默认值，
# 导致前端打包进错误的 API 地址（历史故障：始终指向未启用的 api.clothinkai.com）。
ARG VITE_API_BASE_URL
ARG VITE_SENTRY_DSN
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_SENTRY_DSN=$VITE_SENTRY_DSN

RUN npm run build

# 阶段 2：nginx 静态服务
FROM nginx:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost/ || exit 1
