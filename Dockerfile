# =============================================================================
# LG Appliance Truck Load Optimizer - Dockerfile
# =============================================================================
# Multi-stage build for smaller production image
# Build: docker build -t lg-load-optimizer:0.1.0 .
# Run:   docker run -p 8501:8501 lg-load-optimizer:0.1.0

# ---- Stage 1: Builder ----
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps into a virtualenv (cache-friendly)
COPY requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---- Stage 2: Runtime ----
FROM python:3.12-slim

# Create non-root user
RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --shell /bin/bash --create-home app

WORKDIR /app

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy app code
COPY --chown=app:app . .

# Streamlit config
RUN mkdir -p /home/app/.streamlit && \
    echo "[server]\n\
headless = true\n\
enableCORS = false\n\
enableXsrfProtection = true\n\
maxUploadSize = 20\n\
[browser]\n\
gatherUsageStats = false" > /home/app/.streamlit/config.toml && \
    chown -R app:app /home/app/.streamlit

USER app

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
