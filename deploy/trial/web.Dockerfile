FROM python:3.12-slim-trixie

ARG TRIAL_UID=1000
ARG TRIAL_GID=100
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git socat tini \
    && apt-get clean \
    && useradd --no-log-init --uid "$TRIAL_UID" --gid "$TRIAL_GID" \
        --home-dir /tenant/home trial

WORKDIR /opt/argus
COPY pyproject.toml README.md LICENSE argus_doctor.py ./
COPY argus_skill ./argus_skill
COPY frontend/web/dist ./frontend/web/dist
COPY frontend/tui/bundle/argus.mjs ./frontend/tui/bundle/argus.mjs
RUN pip install --no-cache-dir '.[trial]'
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
CMD ["python", "-m", "argus_skill.trial.web_runtime"]
