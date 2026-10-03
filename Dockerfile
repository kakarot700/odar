# ODAR production container.
# Non-root, no network egress required at runtime beyond research fetches,
# health endpoint on demand, durable volume for the job store.
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/odar

# Runtime dependencies are GOVERNED BY THE LOCKFILE (requirements/prod.lock.txt).
# CPU torch wheel first (keeps the image free of CUDA), then the locked set,
# then the package itself without re-resolving dependencies.
COPY pyproject.toml README.md ./
COPY requirements ./requirements
COPY odar ./odar
RUN pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements/prod.lock.txt \
    && pip install . --no-deps

# Drop privileges: run as a dedicated non-root user.
RUN useradd --create-home --uid 10001 odar \
    && mkdir -p /opt/odar/odar_output /var/lib/odar \
    && chown -R odar:odar /opt/odar /var/lib/odar
USER odar

ENV ODAR_DB=/var/lib/odar/odar_jobs.db
VOLUME ["/var/lib/odar", "/opt/odar/odar_output"]

# Default: health probe.  Override CMD for `run` / `resume` / `status`.
ENTRYPOINT ["python3", "run_research.py"]
CMD ["health"]
