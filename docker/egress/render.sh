#!/bin/sh
# Gera a allowlist do proxy a partir do ANTHROPIC_BASE_URL (recebido só pelo nome) e sobe o squid.
set -eu
: "${ANTHROPIC_BASE_URL:?defina ANTHROPIC_BASE_URL (set -a; source .env; set +a)}"
# esquema://[usuário@]host[:porta][/caminho] -> host
host=$(printf '%s' "$ANTHROPIC_BASE_URL" | sed -E 's#^[A-Za-z][A-Za-z0-9+.-]*://##; s#^[^@/]*@##; s#[/:?].*$##')
[ -n "$host" ] || { echo "ANTHROPIC_BASE_URL sem host" >&2; exit 1; }
printf '%s\n' "$host" > /etc/squid/allowed.txt
if [ "${EGRESS_ALLOW_AWS:-0}" = "1" ]; then
  printf '%s\n' ".amazonaws.com" >> /etc/squid/allowed.txt
fi
tail -F /var/log/squid/access.log /var/log/squid/cache.log 2>/dev/null &
exec /usr/sbin/squid -f /etc/squid/egress.conf -NYC
