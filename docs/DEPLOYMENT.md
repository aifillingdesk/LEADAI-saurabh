# LeadAI Production Deployment Guide

This guide covers production deployment best practices, containerization, environment configuration, database topologies, and horizontal scaling.

---

## 1. Containerization & Dockerfile

LeadAI includes a hardened production Dockerfile designed for container security and high availability.

### Security Highlights:
- **Non-Root Execution**: Runs as user `leadai` (UID 10001).
- **Multi-Stage Build**: Keeps the runtime image lightweight (~180MB) without compilers or dev tools.
- **Docker Healthcheck**: Built-in container health check querying `GET /health` with timeout and retry thresholds.

### Building and Running the Container:
```bash
# Build the production image
docker build -t leadai:latest -f Dockerfile .

# Run the container
docker run -d \
  --name leadai-app \
  -p 8000:8000 \
  --env-file .env.production \
  --restart unless-stopped \
  leadai:latest
```

---

## 2. Production Environment Configuration

In production (`ENV=production`), strict validation guarantees security:
- `SECRET_KEY` must be at least 32 cryptographically random characters.
- `SUPERADMIN_PASSWORD` must be a valid bcrypt hash (`$2b$...`).
- `APIFY_API_TOKEN` and `GEMINI_API_KEY` are strictly required.

### Essential Production Variables
```ini
ENV=production
BASE_URL=https://leadai.yourdomain.com
SECRET_KEY=generate_with_openssl_rand_hex_32
SUPERADMIN_EMAIL=admin@yourdomain.com
SUPERADMIN_PASSWORD=$2b$12$...bcrypt_hash...

# Databases & Caching
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/leadai_prod?retryWrites=true&w=majority&maxPoolSize=50
REDIS_URL=redis://:redis_password@redis-cluster:6379/0

# External APIs
GEMINI_API_KEY=AIzaSy...
APIFY_API_TOKEN=apify_api_...

# Payments & Billing
BILLING_PROVIDER=razorpay
RAZORPAY_KEY_ID=rzp_live_...
RAZORPAY_KEY_SECRET=...
BILLING_WEBHOOK_SECRET=...

# Storage (local, s3, cloudinary)
STORAGE_BACKEND=s3
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=ap-south-1
AWS_S3_BUCKET=leadai-production-media

# Observability
SENTRY_DSN=https://...@sentry.io/...
LOG_LEVEL=INFO
```

---

## 3. Database & Cache Topologies

### MongoDB Atlas / Replica Set
- Minimum recommended topology: 3-node replica set (M10+ on MongoDB Atlas or self-hosted).
- Connection Pool: Configure `maxPoolSize=50` and `minPoolSize=10`.
- Automated Indexing: Run `python -m app.db.ensure_indexes` on deployment to verify compound indexes exist for tenant scoping.

### Redis Cache & Queue
- Use managed Redis (e.g. AWS ElastiCache or Redis Cloud) with TLS enabled.
- Ensure `maxmemory-policy noeviction` on queue databases to guarantee durable job handling.

---

## 4. Background Workers & Horizontal Scaling

LeadAI decouples the web API from background scraping and AI classification workers.

### Architecture Topology:
- **API Web Service**: 2+ replicas running behind an Nginx or cloud load balancer.
- **Queue Workers**: Separate worker containers running the queue processor:
  ```bash
  python -m app.queue.service --worker
  ```
- **Scheduler**: A single leader replica triggering recurring scheduled scans:
  ```bash
  python -m app.services.scheduler --cron
  ```

---

## 5. Reverse Proxy & SSL (Nginx Configuration)

```nginx
server {
    listen 443 ssl http2;
    server_name leadai.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/leadai.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/leadai.yourdomain.com/privkey.pem;

    client_max_body_size 25M;

    # Gzip & Brotli compression
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml;

    location /static/ {
        alias /app/static/;
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```
