FROM python:3.11-slim

WORKDIR /app

# Runtime deps. uv is a dev convenience; not in the image.
COPY pyproject.toml ./
RUN pip install --no-cache-dir httpx pydantic pyyaml

COPY src/ ./src/

# Make /app world-readable so any UID OpenShift assigns can run the code.
# (OpenShift's restricted SCC ignores hardcoded USER directives and runs
# containers as a random UID in the namespace range. We avoid root and rely
# on group-readable / world-readable file modes.)
RUN chgrp -R 0 /app && chmod -R g=u /app

USER 1001

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# CONFIG_PATH defaults to /etc/service-watch/config.yaml (matches the
# ConfigMap mount path in the Deployment).
ENTRYPOINT ["python", "-m", "service_watch"]
