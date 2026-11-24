# Playwright's official image ships Chromium + all OS deps, matched to the
# playwright npm version below. Stem Borer renders target pages headless to
# discover JS-loaded media URLs.
FROM mcr.microsoft.com/playwright:v1.48.2-noble

WORKDIR /app
ENV NODE_ENV=production

# Install deps (include dev deps so `next build` has TypeScript/types).
COPY package.json package-lock.json* ./
RUN npm install --include=dev

# Build the app.
COPY . .
RUN npm run build

EXPOSE 3000
CMD ["npm", "run", "start"]
