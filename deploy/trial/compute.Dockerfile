ARG WEB_BASE_IMAGE=argus-web-trial:20260911
FROM python:3.12-slim-trixie AS packages
RUN pip install --no-cache-dir --target /opt/compute torch==2.8.0 numpy pandas scipy scikit-learn matplotlib \
    datasets transformers accelerate safetensors
FROM ${WEB_BASE_IMAGE}
COPY --from=packages /opt/compute /opt/compute
ENV PYTHONPATH=/opt/argus:/opt/compute
USER trial
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "python", "-m", "argus_skill.trial.job_runtime"]
