FROM mcr.microsoft.com/playwright@sha256:b0ab6f3cb99aa7803adbc14d9027ec1785fc6e433b97e134e0f8fe61683b6b53

WORKDIR /probe
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && node --input-type=module -e "const { chromium } = await import('playwright'); const api = await import('@actual-app/api'); if (typeof chromium?.launch !== 'function' || typeof api.downloadBudget !== 'function' || typeof api.sync !== 'function') throw new Error('probe dependency interface missing')"

COPY actual-ui-sync-probe.mjs expected-envelope.json expected-envelope.contract.json ./
RUN chmod 0755 /probe/actual-ui-sync-probe.mjs \
    && chmod 0600 /probe/expected-envelope.json /probe/expected-envelope.contract.json \
    && find /probe -type d -name __pycache__ -prune -exec rm -rf -- {} + \
    && test -z "$(find /probe -type d -name __pycache__ -print -quit)"

RUN node --input-type=module -e "const { chromium } = await import('playwright'); const api = await import('@actual-app/api'); if (typeof chromium?.launch !== 'function' || typeof api.downloadBudget !== 'function' || typeof api.sync !== 'function') throw new Error('probe dependency interface missing')"
