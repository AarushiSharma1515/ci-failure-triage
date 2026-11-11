# Small official Python image (same minor version as local development).
FROM python:3.12-slim

# Don't write .pyc files; print logs straight away. PORT can be overridden at run time.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Install dependencies first so this layer is cached when only code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only what the service needs at run time.
COPY triage/ triage/
COPY app/ app/
COPY data/samples/ data/samples/

# Run as an unprivileged user, not root.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# Docker marks the container unhealthy if /health stops answering.
# (python is used instead of curl because curl is not installed in slim images.)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health', timeout=4)"]

# "sh -c" lets ${PORT} be read from the environment; "exec" makes uvicorn receive stop signals.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
