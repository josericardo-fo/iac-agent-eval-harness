# Imagem do agente do E3: a toolchain (Terraform, AWS CLI, boto3, mirror de providers) + Node LTS,
# Claude Code CLI em versão fixa, git e um usuário não-root (como root, o bypassPermissions é
# recusado). Build: docker build -f docker/agent.Dockerfile -t iac-harness-agent:latest .
FROM iac-harness-toolchain:latest

# Node LTS (Krypton) e Claude Code na versão do host na data da implementação.
ARG NODE_VERSION=24.22.2
ARG CLAUDE_CODE_VERSION=2.1.314
ARG TARGETARCH

RUN apt-get update \
    && apt-get install -y --no-install-recommends git xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Node, com verificação do SHASUMS256 oficial.
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) arch=x64 ;; \
      arm64) arch=arm64 ;; \
      *) echo "arquitetura sem Node: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    tarball="node-v${NODE_VERSION}-linux-${arch}.tar.xz"; \
    cd /tmp; \
    curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/${tarball}"; \
    curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt"; \
    grep " ${tarball}\$" SHASUMS256.txt | sha256sum -c -; \
    tar -xJf "${tarball}" -C /usr/local --strip-components=1 --no-same-owner; \
    rm -f "/tmp/${tarball}" /tmp/SHASUMS256.txt; \
    node --version

RUN npm install -g --no-fund --no-audit "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force \
    && claude --version

# O .terraformrc da toolchain fica em /root (ilegível para o agente): cópia legível em /etc.
RUN cp /root/.terraformrc /etc/terraformrc && chmod 644 /etc/terraformrc

RUN useradd --create-home --uid 1000 --shell /bin/bash agent \
    && mkdir -p /work && chown agent:agent /work

# Sem autoupdate, telemetria nem tráfego não essencial (o proxy de egresso bloquearia de todo modo).
ENV HOME=/home/agent \
    PATH=/opt/py/bin:${PATH} \
    TF_CLI_CONFIG_FILE=/etc/terraformrc \
    DISABLE_AUTOUPDATER=1 \
    DISABLE_TELEMETRY=1 \
    DISABLE_ERROR_REPORTING=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

USER agent
WORKDIR /work
