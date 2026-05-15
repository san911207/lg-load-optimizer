# Deployment Guide

## Local development

```bash
# Clone & install
git clone <repo>
cd load_optimizer
python -m venv venv && source venv/bin/activate  # or use uv/poetry
pip install -r requirements.txt

# Run Streamlit PoC
streamlit run app.py
# → http://localhost:8501
```

## Docker (recommended for shared environments)

```bash
# Build & start
docker-compose up --build

# Detached
docker-compose up -d

# Logs
docker-compose logs -f app

# Stop
docker-compose down
```

The compose file starts:
- `app` — Streamlit server on port 8501
- `db` — PostgreSQL 16 on port 5432 (for Phase 1+, currently unused)

## Company cloud deployment (Phase 1)

### Prerequisites
- Container registry access (ECR / Artifact Registry / ACR)
- Kubernetes cluster or App Service / ECS / Cloud Run
- Postgres instance (RDS / Cloud SQL / Azure Database)
- SSO provider (Okta / Azure AD / Auth0)
- Domain + TLS cert

### Build & push

```bash
# Tag for your registry
docker build -t lg-load-optimizer:0.1.0 .
docker tag lg-load-optimizer:0.1.0 <registry>/lg-load-optimizer:0.1.0
docker push <registry>/lg-load-optimizer:0.1.0
```

### Environment variables

```env
# .env (do NOT commit)
DATABASE_URL=postgresql://user:pass@db-host:5432/load_optimizer
SECRET_KEY=<generate with: openssl rand -hex 32>
ENV=production
LOG_LEVEL=INFO

# Phase 1: SSO
SSO_PROVIDER=saml
SAML_IDP_URL=https://lg.okta.com/...
SAML_CERT_PATH=/etc/ssl/certs/saml.crt

# Phase 1: Storage
S3_BUCKET=lg-load-optimizer-prod
S3_REGION=us-east-1
```

Use the secret manager of your cloud (AWS Secrets Manager, Azure Key Vault, GCP Secret Manager) — never store secrets in the image.

### Kubernetes example

```yaml
# k8s/deployment.yaml (template — adjust for your cluster)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: load-optimizer
spec:
  replicas: 2
  selector:
    matchLabels:
      app: load-optimizer
  template:
    metadata:
      labels:
        app: load-optimizer
    spec:
      containers:
      - name: app
        image: <registry>/lg-load-optimizer:0.1.0
        ports:
        - containerPort: 8501
        envFrom:
        - secretRef:
            name: load-optimizer-secrets
        resources:
          requests:
            memory: "256Mi"
            cpu: "200m"
          limits:
            memory: "1Gi"
            cpu: "1000m"
        livenessProbe:
          httpGet:
            path: /_stcore/health
            port: 8501
          initialDelaySeconds: 30
---
apiVersion: v1
kind: Service
metadata:
  name: load-optimizer
spec:
  selector:
    app: load-optimizer
  ports:
  - port: 80
    targetPort: 8501
  type: ClusterIP
```

### SSO (Phase 1)

For LG corporate SSO, options in order of preference:

1. **SAML 2.0** — integrate via Okta or Azure AD as IdP. Use `python-saml` library
2. **OAuth/OIDC** — use `authlib` with company OIDC endpoint
3. **Reverse proxy** — let nginx/oauth2-proxy handle auth before reaching the app

```python
# Example: oauth2-proxy in front of Streamlit
# All traffic → oauth2-proxy → checks SSO → passes user header to Streamlit
```

### Health checks & monitoring

- `/_stcore/health` — Streamlit built-in
- Phase 1: add `/api/health` endpoint to FastAPI
- Metrics: Prometheus scraping at `/metrics` (use `prometheus-client`)
- Logs: structured JSON to stdout, ship to Datadog / CloudWatch / Stackdriver

### Backup & disaster recovery

- DB backups: daily, retain 30 days (use cloud-managed Postgres automated backups)
- Model master data: version-controlled in Git (the source of truth is `data/sample_input.xlsx` + DB)
- Output reports: optional — Phase 1 stores generated PDFs to S3 for audit (90-day retention)

## Performance

PoC handles 100+ loads/day easily on a single 1 vCPU / 1 GB pod.

For Phase 1 with multiple users:
- Stateless, horizontally scalable (no in-memory state between requests)
- Postgres can handle 1000s of loads/day with proper indexing on `load_id`
- 3D rendering moved to client-side (React + Three.js) → no server-side rendering load

## Security checklist

- [ ] No secrets in container image
- [ ] TLS everywhere (TLS 1.3 preferred)
- [ ] SSO required for all endpoints
- [ ] CORS locked down to LG domains only
- [ ] Input validation on all user uploads (Excel files)
- [ ] File size limits (10 MB max per Excel)
- [ ] Rate limiting on simulation endpoint (Phase 1)
- [ ] Audit log of all simulations (who ran what, when)
- [ ] No PII collected (load data is shipment quantities, not personal data)
- [ ] Container runs as non-root user
- [ ] Image scanning in CI (Trivy or Snyk)

## CI/CD

Recommended GitHub Actions workflow:

```yaml
# .github/workflows/deploy.yml
name: Deploy
on:
  push:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: pytest tests/
  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/build-push-action@v5
        with:
          push: true
          tags: <registry>/lg-load-optimizer:${{ github.sha }}
  deploy:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - run: kubectl set image deployment/load-optimizer app=<registry>/lg-load-optimizer:${{ github.sha }}
```

## Rollback

If a deployment goes wrong:

```bash
# Kubernetes
kubectl rollout undo deployment/load-optimizer

# Or pin to previous tag
kubectl set image deployment/load-optimizer app=<registry>/lg-load-optimizer:0.0.9
```

Keep last 5 image tags in registry for safe rollback.
