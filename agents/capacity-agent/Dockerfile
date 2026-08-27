# Capacity agent image: Google ADK + read-only k8s client + nono.
#
# nono comes from the official image so in-cluster and on-laptop runs use
# the same sandbox (Landlock in the pod / on a Linux dev box, Seatbelt on a
# Mac — same profile JSON either way).
ARG NONO_IMAGE=ghcr.io/nolabs-ai/nono:latest
FROM ${NONO_IMAGE} AS nono

FROM python:3.12-slim
COPY --from=nono /usr/bin/nono /usr/bin/nono

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

RUN useradd --uid 65532 --user-group --create-home --shell /usr/sbin/nologin agent
COPY app/ /app/

ENV HOME=/home/agent \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER 65532:65532

# The Deployment wraps this in `nono run --profile ... --` so the agent
# process never runs unconfined; keep the image entrypoint bare for local
# experimentation.
CMD ["python", "/app/main.py"]
