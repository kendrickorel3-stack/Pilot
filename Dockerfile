# Hosted web app. Set ANTHROPIC_API_KEY and INVOICE_AGENT_ACCESS_CODE at deploy time.
FROM mcr.microsoft.com/playwright/python:v1.55.0-noble
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt fastapi uvicorn && python -m playwright install chromium
COPY invoice_agent ./invoice_agent
ENV INVOICE_AGENT_DATA=/data PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "python -m invoice_agent.server --host 0.0.0.0 --port ${PORT}"]
