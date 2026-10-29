FROM python:3.11.18-slim-bookworm@sha256:061c190f3d5815b341a18dfe8a45771f8ba68c15a607ea56997fde3009ac4eec

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models \
    HF_HUB_CACHE=/models/hub \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TRANSFORMERS_OFFLINE=1 \
    OMP_NUM_THREADS=2 \
    MKL_NUM_THREADS=2

WORKDIR /app

COPY services/retrieval/requirements.lock /app/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /app/requirements.lock

COPY services/retrieval/retrieval_worker /app/retrieval_worker

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin worker
USER 10001:10001

EXPOSE 8080
CMD ["python", "-m", "retrieval_worker.app", "--role", "embedding"]
