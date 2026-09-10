FROM node:22-alpine AS builder

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

ARG NEXT_PUBLIC_QUANT_API_URL=https://quant-signal-api.zeabur.app
ENV NEXT_PUBLIC_QUANT_API_URL=${NEXT_PUBLIC_QUANT_API_URL}
RUN npm run build

FROM node:22-alpine AS runner

WORKDIR /app
ENV NODE_ENV=production
ENV HOST=0.0.0.0
ENV PORT=3000

COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.ts /app/vite.config.ts ./
EXPOSE 3000

CMD ["npm", "run", "start", "--", "--host", "0.0.0.0"]
