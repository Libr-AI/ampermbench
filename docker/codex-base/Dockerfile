ARG CODEX_VERSION=0.104.0

FROM ubuntu:24.04
ARG CODEX_VERSION
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    coreutils \
    curl \
    git \
    iptables \
    jq \
    nodejs \
    npm \
    python3 \
    python3-venv \
    util-linux \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g "@openai/codex@${CODEX_VERSION}" \
    && codex --version
RUN useradd --create-home --uid 1001 bench
WORKDIR /opt/ampermbench
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
COPY pyproject.toml /opt/ampermbench/pyproject.toml
COPY src /opt/ampermbench/src
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir .
COPY scripts/container-entrypoint.sh /usr/local/bin/ampermbench-entrypoint
RUN chmod +x /usr/local/bin/ampermbench-entrypoint
ENTRYPOINT ["/usr/local/bin/ampermbench-entrypoint"]
CMD ["bash"]
