FROM python:3.11-slim-bookworm

RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends \
       bash ca-certificates git libstdc++6 ripgrep \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /opt/study/requirements.txt
RUN pip install --no-cache-dir -q -r /opt/study/requirements.txt \
    && pip freeze > /opt/study/dependencies.txt
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GIT_TERMINAL_PROMPT=0 \
    GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_GLOBAL=/dev/null \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1
WORKDIR /work
