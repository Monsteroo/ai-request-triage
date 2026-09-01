# python:3.12-slim rather than :latest — the image should not change under us
# between a local build and a CI build three weeks from now.
FROM python:3.12-slim

# Never write .pyc into the layer; never buffer logs behind a dead container.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt actually moves.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY data/ ./data/

# Run unprivileged, and make sure the output mount point is writable by that user.
RUN useradd --create-home --uid 10001 triage \
    && mkdir -p /app/output \
    && chown -R triage:triage /app/output
USER triage

ENTRYPOINT ["python", "-m", "triage"]
