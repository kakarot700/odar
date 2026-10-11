# ODAR on a free Hugging Face Docker Space (2 vCPU, 16 GB RAM, CPU only).
# Build context: the repo root. The Space serves the web app on port 7860.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/tmp/hf ODAR_WEB_DB=/tmp/odar/odar_web.db ODAR_DB=/tmp/odar/odar_jobs.db \
    ODAR_TRUST_PROXY=1

WORKDIR /opt/odar
COPY pyproject.toml README.md ./
COPY requirements ./requirements
COPY odar ./odar
COPY run_research.py ./
RUN pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements/prod.lock.txt \
    && pip install "fastapi>=0.110" "uvicorn>=0.29" "python-multipart>=0.0.9" "fpdf2>=2.7" "pypdf>=5.0" \
    && pip install . --no-deps

# Spaces run the container as uid 1000.
RUN useradd --create-home --uid 1000 user && mkdir -p /tmp/odar /tmp/hf && chown -R user /tmp/odar /tmp/hf /opt/odar
USER user
EXPOSE 7860
CMD ["python3", "-m", "odar.cli", "serve", "--host", "0.0.0.0", "--port", "7860"]
