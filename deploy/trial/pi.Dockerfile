ARG WEB_BASE_IMAGE=argus-web-trial:20260911
FROM ${WEB_BASE_IMAGE}
ARG ARGUS_SKILL_BUILD_REVISION=""
ENV ARGUS_SKILL_BUILD_REVISION=${ARGUS_SKILL_BUILD_REVISION}
USER root
COPY --from=node-runtime /node /usr/local/bin/node
COPY --from=argus-pi /package.json /opt/argus-pi/package.json
COPY --from=argus-pi /node_modules /opt/argus-pi/node_modules
COPY --from=argus-pi /packages /opt/argus-pi/packages
COPY argus /opt/argus/argus
COPY frontend/web/dist /opt/argus/frontend/web/dist
COPY frontend/tui/bundle/argus.mjs /opt/argus/frontend/tui/bundle/argus.mjs
# WEB_BASE_IMAGE may be an older prepared image without the shared search tool.
RUN if ! command -v rg >/dev/null 2>&1; then \
        apt-get update \
        && apt-get install -y --no-install-recommends ripgrep \
        && apt-get clean; \
    fi \
    && pip install --no-cache-dir 'psutil>=5.9.8' \
    && chmod -R a+rX /opt/argus/argus /opt/argus/frontend \
    && ln -s /opt/argus-pi/packages/coding-agent/dist/bundle/cli.js /usr/local/bin/argus-pi \
    && ln -s /usr/local/bin/argus-pi /usr/local/bin/pi \
    && node --version && argus-pi --version && rg --version
ENV ARGUS_TRIAL_HARNESS=argus-pi \
    ARGUS_SKILL_COPILOT_TRIAL=0 \
    ARGUS_SKILL_RUNNER_BACKEND=pi \
    ARGUS_SKILL_LIFE_BACKEND=pi \
    ARGUS_SKILL_RUNNER_BIN=/usr/local/bin/argus-pi \
    ARGUS_SKILL_BACKEND_AUTH_MODE=subscription_cli \
    ARGUS_SKILL_PI_PROVIDER=argus \
    PI_CODING_AGENT_DIR=/tenant/home/.argus-skill/argus-pi \
    PI_HARNESS_PROFILE=argus \
    PI_OFFLINE=1 \
    NODE_USE_ENV_PROXY=1
USER trial
