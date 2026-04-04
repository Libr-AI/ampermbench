#!/usr/bin/env bash
set -euo pipefail

export PATH="/bench/bin:/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

AGENT_USER="${AMPERMBENCH_AGENT_USER:-bench}"
AGENT_UID="${AMPERMBENCH_UID:-}"
AGENT_GID="${AMPERMBENCH_GID:-}"
BENCH_ACTOR="${BENCH_ACTOR:-alex}"
ALLOW_IPS="${BENCH_ALLOW_EGRESS_IPS:-}"
ALLOW_ENDPOINTS="${BENCH_ALLOW_EGRESS_ENDPOINTS:-}"

allow_tcp_egress() {
  local destination="$1"
  local port="$2"
  local firewall_bin="iptables"
  if [[ "${destination}" == *:* ]]; then
    firewall_bin="ip6tables"
  fi
  "${firewall_bin}" -A OUTPUT -d "${destination}" -p tcp --dport "${port}" -j ACCEPT
}

if [[ ( -n "${ALLOW_IPS}" || -n "${ALLOW_ENDPOINTS}" ) && "$(id -u)" -eq 0 ]]; then
  iptables -P OUTPUT DROP
  iptables -A OUTPUT -o lo -j ACCEPT
  iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  ip6tables -P OUTPUT DROP
  ip6tables -A OUTPUT -o lo -j ACCEPT
  ip6tables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  IFS=',' read -r -a ip_array <<< "${ALLOW_IPS}"
  for ip in "${ip_array[@]}"; do
    [[ -z "${ip}" ]] && continue
    allow_tcp_egress "${ip}" 443
  done
  IFS=',' read -r -a endpoint_array <<< "${ALLOW_ENDPOINTS}"
  for endpoint in "${endpoint_array[@]}"; do
    [[ -z "${endpoint}" ]] && continue
    host="${endpoint%:*}"
    port="${endpoint##*:}"
    [[ -z "${host}" || -z "${port}" ]] && continue
    allow_tcp_egress "${host}" "${port}"
  done
fi
export BENCH_ACTOR

if [[ "$(id -u)" -eq 0 ]]; then
  if [[ -n "${AGENT_UID}" && -n "${AGENT_GID}" ]]; then
    export USER="${BENCH_ACTOR}"
    exec setpriv --reuid "${AGENT_UID}" --regid "${AGENT_GID}" --clear-groups -- "$@"
  fi
  exec runuser -u "${AGENT_USER}" -- "$@"
fi

exec "$@"
