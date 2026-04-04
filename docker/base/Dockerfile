ARG CLAUDE_VERSION=2.1.86

FROM ubuntu:24.04 AS claude-builder
ARG CLAUDE_VERSION
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    coreutils \
    curl \
    git \
    jq \
    python3 \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*
COPY docker/base/install.sh /tmp/claude-install.sh
RUN sed -i 's/\r$//' /tmp/claude-install.sh \
    && chmod +x /tmp/claude-install.sh \
    && head -n 1 /tmp/claude-install.sh | grep -Eq '^#!/' \
    || (echo "docker/base/install.sh is not a valid shell installer." >&2 && exit 1)
RUN bash /tmp/claude-install.sh "${CLAUDE_VERSION}" \
    && CLAUDE_BIN="$(readlink -f /root/.local/bin/claude)" \
    && test -n "${CLAUDE_BIN}" \
    && test -x "${CLAUDE_BIN}" \
    && install -m 0755 "${CLAUDE_BIN}" /tmp/claude

FROM ubuntu:24.04
ARG CLAUDE_VERSION
ENV DEBIAN_FRONTEND=noninteractive
ENV DISABLE_AUTOUPDATER=true
ENV TZ=Etc/UTC
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    coreutils \
    curl \
    git \
    iptables \
    jq \
    python3 \
    python3-venv \
    util-linux \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1001 bench
WORKDIR /opt/ampermbench
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
COPY pyproject.toml /opt/ampermbench/pyproject.toml
COPY src /opt/ampermbench/src
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir .
COPY --from=claude-builder /tmp/claude /usr/local/bin/claude
COPY scripts/container-entrypoint.sh /usr/local/bin/ampermbench-entrypoint
RUN chmod +x /usr/local/bin/claude /usr/local/bin/ampermbench-entrypoint
ENTRYPOINT ["/usr/local/bin/ampermbench-entrypoint"]
CMD ["bash"]
