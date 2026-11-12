# Pilot web app, for hosts like Railway, Render or Fly.io.
# Required: INVOICE_AGENT_ACCESS_CODE (everyone using the site needs it).
# The AI key can be set on the site's setup screen, or as OPENAI_API_KEY / ANTHROPIC_API_KEY.
# Mount a volume at /data to keep keys, rules, downloads and reports across redeploys.
FROM mcr.microsoft.com/playwright/python:v1.65.0-noble
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium
COPY invoice_agent ./invoice_agent
ENV INVOICE_AGENT_DATA=/data PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh", "-c", "python -m invoice_agent.server --host 0.0.0.0 --port ${PORT}"]
