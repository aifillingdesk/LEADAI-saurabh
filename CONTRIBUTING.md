# Contributing to LeadAI

Thank you for your interest in contributing to LeadAI! LeadAI is an enterprise-grade multi-tenant SaaS application that automates social listening, comment scraping, and AI-driven buyer intent classification.

---

## Code of Conduct

We are committed to providing a friendly, safe, and welcoming environment for all contributors. Please treat everyone with respect and empathy.

---

## Development Setup

### 1. Prerequisites
- **Python**: 3.11, 3.12, or 3.14
- **MongoDB**: 6.0+ (or MongoDB Atlas)
- **Redis**: 7.0+ (optional for local dev, queue worker falls back to memory)
- **Git**

### 2. Clone and Setup Environment
```bash
git clone https://github.com/your-org/lead_apify.git
cd lead_apify

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
pip install pytest ruff mypy
```

### 3. Environment Variables
Copy the example environment file:
```bash
cp .env.example .env
```
Fill in the minimal local variables:
```ini
ENV=development
SECRET_KEY=dev_secret_key_32_characters_minimum_example
MONGODB_URI=mongodb://localhost:27017/leadai_dev
GEMINI_API_KEY=your_gemini_key
APIFY_API_TOKEN=your_apify_token
```

---

## Coding Guidelines

1. **Strict Multi-Tenancy**:
   - Every tenant query MUST be scoped with `organization_id`.
   - Never query or update documents across tenants without authorization checks.
   - Use `find_scoped_or_404` or `scope_query` from `app.auth.tenant`.

2. **Security & Secrets**:
   - Never commit API keys, secrets, or credential tokens.
   - Use constant-time comparison (`hmac.compare_digest`) for signatures and tokens.
   - Hash all sensitive API keys and tokens with SHA-256 before storing in the database.

3. **Code Quality & Formatting**:
   - Format and lint code using `ruff`:
     ```bash
     ruff check .
     ```
   - Type check critical modules:
     ```bash
     mypy app/
     ```

4. **Testing Standards**:
   - Always run the test suite before submitting pull requests:
     ```bash
     pytest
     ```
   - All tests must pass 100%. Add regression tests for any bug fixes and unit tests for new features.

---

## Pull Request Workflow

1. Create a feature branch from `main`:
   ```bash
   git checkout -b feat/your-feature-name
   ```
2. Commit changes with conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`).
3. Ensure CI passes on GitHub Actions (ruff, mypy, pytest).
4. Submit PR with a clear summary of changes and testing steps.
