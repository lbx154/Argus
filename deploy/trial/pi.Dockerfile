ARG WEB_BASE_IMAGE=argus-web-trial:20260911
FROM ${WEB_BASE_IMAGE}
USER root
COPY --from=node-runtime /node /usr/local/bin/node
COPY --from=argus-pi /package.json /opt/argus-pi/package.json
COPY --from=argus-pi /node_modules /opt/argus-pi/node_modules
COPY --from=argus-pi /packages /opt/argus-pi/packages
COPY argus_skill /opt/argus/argus_skill
RUN pip install --no-cache-dir 'psutil>=5.9.8' \
    && ln -s /opt/argus-pi/packages/coding-agent/dist/bundle/cli.js /usr/local/bin/argus-pi \
    && ln -s /usr/local/bin/argus-pi /usr/local/bin/pi \
    && node --version && argus-pi --version
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
