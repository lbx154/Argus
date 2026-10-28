FROM python:3.12-slim-trixie

ARG ARGUS_SKILL_BUILD_REVISION=""
ENV ARGUS_SKILL_BUILD_REVISION=${ARGUS_SKILL_BUILD_REVISION}

ARG TRIAL_UID=1000
ARG TRIAL_GID=100
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git ripgrep socat tini \
    && apt-get clean \
    && useradd --no-log-init --uid "$TRIAL_UID" --gid "$TRIAL_GID" \
        --home-dir /tenant/home trial

WORKDIR /opt/argus
COPY pyproject.toml README.md LICENSE argus_doctor.py ./
COPY argus ./argus
COPY frontend/web/dist ./frontend/web/dist
COPY frontend/tui/bundle/argus.mjs ./frontend/tui/bundle/argus.mjs
# The hosted trial server is its own package beside Argus; installed editable so
# the training observer stays at the path the bridge verifies.
COPY trial_server/pyproject.toml ./trial_server/pyproject.toml
COPY trial_server/argus_trial_server ./trial_server/argus_trial_server
RUN pip install --no-cache-dir . && pip install --no-cache-dir -e ./trial_server
COPY --from=runtime-tools /copilot /usr/local/bin/copilot

ENV HOME=/tenant/home \
    ARGUS_SKILL_HOME=/tenant/home/.argus-skill \
    ARGUS_SKILL_COPILOT_TRIAL=1 \
    ARGUS_SKILL_RUNNER_BACKEND=copilot \
    ARGUS_SKILL_LIFE_BACKEND=copilot \
    ARGUS_SKILL_RUNNER_BIN=/usr/local/bin/copilot \
    ARGUS_SKILL_MODEL=gpt-5.5 \
    ARGUS_SKILL_BACKEND_AUTH_MODE=subscription_cli \
    PYTHONPATH=/opt/argus \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER trial
WORKDIR /tenant/workspace
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "argus_trial_server.web_runtime"]
