# Toolchain do harness: Terraform + OPA com versões exatas e providers pré-baixados,
# para que `terraform init` funcione sem internet (rede Docker `internal`). Traz também AWS CLI v2
# e python3 + boto3 para as modalidades imperativas (src/iac_harness/modalities).
FROM debian:bookworm-slim

# Terraform 1.15.x: última patch verificada em releases.hashicorp.com na data da implementação.
ARG TERRAFORM_VERSION=1.15.9
ARG OPA_VERSION=1.16.1
# Providers fixados (versões exatas do mirror validado; protocolo §10).
ARG AWS_PROVIDER_VERSION=6.66.0
ARG ARCHIVE_PROVIDER_VERSION=2.8.1
ARG RANDOM_PROVIDER_VERSION=3.9.1
# awscc (Cloud Control): amplia as triplas utilizáveis do Multi-IaC-Eval.
ARG AWSCC_PROVIDER_VERSION=1.106.0
# AWS CLI v2 e boto3: últimas versões publicadas na data da implementação.
ARG AWSCLI_VERSION=2.38.2
ARG BOTO3_VERSION=1.43.115
# Fingerprint da chave "AWS CLI Team <aws-cli@amazon.com>" (docker/aws-cli-team.asc).
ARG AWSCLI_GPG_FINGERPRINT=FB5DB77FD5C118B80511ADA8A6310ACC4672475C
ARG TARGETARCH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip gnupg \
       python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Terraform, com verificação de checksum.
RUN set -eux; \
    base="https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}"; \
    zip="terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip"; \
    cd /tmp; \
    curl -fsSLO "${base}/${zip}"; \
    curl -fsSLO "${base}/terraform_${TERRAFORM_VERSION}_SHA256SUMS"; \
    grep " ${zip}\$" "terraform_${TERRAFORM_VERSION}_SHA256SUMS" | sha256sum -c -; \
    unzip -d /usr/local/bin "${zip}" terraform; \
    rm -f /tmp/*.zip /tmp/*SHA256SUMS

# OPA (binário estático).
RUN curl -fsSLo /usr/local/bin/opa \
    "https://github.com/open-policy-agent/opa/releases/download/v${OPA_VERSION}/opa_linux_${TARGETARCH}_static" \
    && chmod +x /usr/local/bin/opa

# AWS CLI v2. A AWS não publica sha256 do zip, só a assinatura GPG (.sig); a chave pública vem
# da página oficial de instalação, fica versionada no repo e tem o fingerprint conferido aqui.
COPY docker/aws-cli-team.asc /tmp/aws-cli-team.asc
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) arch=x86_64 ;; \
      arm64) arch=aarch64 ;; \
      *) echo "arquitetura sem AWS CLI: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    zip="awscli-exe-linux-${arch}-${AWSCLI_VERSION}.zip"; \
    cd /tmp; \
    export GNUPGHOME=/tmp/gnupg; mkdir -m 700 "${GNUPGHOME}"; \
    gpg --batch --import /tmp/aws-cli-team.asc; \
    gpg --batch --list-keys --with-colons | grep -q "^fpr:::::::::${AWSCLI_GPG_FINGERPRINT}:"; \
    curl -fsSLO "https://awscli.amazonaws.com/${zip}"; \
    curl -fsSLO "https://awscli.amazonaws.com/${zip}.sig"; \
    gpg --batch --verify "${zip}.sig" "${zip}"; \
    unzip -q "${zip}"; \
    ./aws/install; \
    rm -rf /tmp/aws /tmp/*.zip /tmp/*.sig /tmp/aws-cli-team.asc "${GNUPGHOME}"; \
    aws --version

# python3 + boto3 num venv (versão fixa); o python3 do venv vem primeiro no PATH.
RUN python3 -m venv /opt/py \
    && /opt/py/bin/pip install --no-cache-dir "boto3==${BOTO3_VERSION}" \
    && /opt/py/bin/python3 -c "import boto3; print(boto3.__version__)"
ENV PATH=/opt/py/bin:${PATH}

# Filesystem mirror com os providers usados pelas referências do IaC-Eval v2 (+ awscc do
# Multi-IaC-Eval).
RUN <<'EOF'
set -eux
mkdir -p /tmp/providers /opt/terraform-providers
cat > /tmp/providers/main.tf <<TF
terraform {
  required_providers {
    aws     = { source = "hashicorp/aws", version = "${AWS_PROVIDER_VERSION}" }
    archive = { source = "hashicorp/archive", version = "${ARCHIVE_PROVIDER_VERSION}" }
    random  = { source = "hashicorp/random", version = "${RANDOM_PROVIDER_VERSION}" }
    awscc   = { source = "hashicorp/awscc", version = "${AWSCC_PROVIDER_VERSION}" }
  }
}
TF
cd /tmp/providers
terraform providers mirror /opt/terraform-providers
rm -rf /tmp/providers
cat > /root/.terraformrc <<'RC'
provider_installation {
  filesystem_mirror {
    path    = "/opt/terraform-providers"
    include = ["registry.terraform.io/*/*"]
  }
}
RC
EOF

ENV TF_CLI_CONFIG_FILE=/root/.terraformrc \
    TF_IN_AUTOMATION=1 \
    CHECKPOINT_DISABLE=1

WORKDIR /work
