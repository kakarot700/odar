# Deploying ODAR

ODAR is one Docker container: the FastAPI web app plus a local NLI model
(`cross-encoder/nli-deberta-v3-small`, CPU torch) that checks every citation.

## Memory (measured 2026-10-11, CPU, Python 3.12)

| State | RSS |
| --- | --- |
| Web server idle, before the checker loads | ~100 MB |
| After the NLI checker loads | ~590 MB |
| Peak while checking a batch of 32 long passages | ~1.4 GB |

Plan for **2 GB RAM minimum** (one user at a time) and **4 GB** for a few
concurrent users. 512 MB free tiers (Render, Koyeb) cannot load the checker.
2 vCPUs keep a check under ~10 s; one vCPU roughly doubles that. Image size is
about 2 GB (CPU torch). First boot downloads the ~550 MB model unless
`HF_HOME` points at a persistent disk.

## Run

    docker build -f deploy/hf-space/Dockerfile -t odar .
    docker run -p 7860:7860 \
      -e ANTHROPIC_API_KEY=<Token Harbor key> \
      -e ODAR_ANTHROPIC_BASE_URL=https://tokenharbor.ai \
      -e ODAR_CONTACT_EMAIL=<your email> \
      -v odar-data:/tmp/odar odar

The image listens on port 7860 (Hugging Face convention); any host that runs a
Dockerfile works (a VPS, Fly, Railway, Cloud Run, a Hugging Face PRO Space).

## Environment

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | model key (Token Harbor free models by default) |
| `ODAR_ANTHROPIC_BASE_URL` | `https://tokenharbor.ai` |
| `ODAR_MODEL_ROUTES` | optional JSON per-role model override |
| `ODAR_WEB_DB` | SQLite run history path (put it on a persistent volume) |
| `ODAR_TRUST_PROXY=1` | trust `X-Forwarded-For` behind the host's proxy |
| `ODAR_KEY_DAILY_LIMIT`, `ODAR_ASK_PER_HOUR` | rate limits |
| `ODAR_CONTACT_EMAIL` | polite contact for OpenAlex / Crossref |

## Models

Default routes use Token Harbor `:free` models. `mimo-v2.5:free` drops out
automatically on its 2026-10-21 retirement and `mimo-v2.6-flash:free` /
`deepseek-v4-flash:free` take over (see `odar/router.py`). Free quotas are
small: a public launch needs a paid or higher-limit model key.
