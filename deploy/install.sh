#!/usr/bin/env bash
# One-shot installer for the PcController rendezvous / signaling server.
# Run on a fresh Ubuntu/Debian server as root:
#
#     bash install.sh [PORT]          # PORT defaults to 50510
#
# or straight from GitHub:
#
#     curl -fsSL https://raw.githubusercontent.com/LuckyYouStudio/PcController/main/deploy/install.sh | bash -s -- 50510
#
# The server is pure Python 3 standard library — no pip installs needed.
set -euo pipefail

PORT="${1:-50510}"
REPO="https://github.com/LuckyYouStudio/PcController.git"
DIR="/opt/PcController"

echo "==> installing python3 + git"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y python3 git
elif command -v yum >/dev/null 2>&1; then
  yum install -y python3 git
fi

echo "==> fetching code into $DIR"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only || true
else
  git clone --depth 1 "$REPO" "$DIR"
fi

echo "==> installing systemd service (port $PORT)"
sed "s#{{DIR}}#${DIR}#g; s#{{PORT}}#${PORT}#g" \
  "$DIR/deploy/pccontroller-relay.service" \
  > /etc/systemd/system/pccontroller-relay.service
systemctl daemon-reload
systemctl enable --now pccontroller-relay

echo "==> opening host firewall if one is active (TCP + UDP)"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "${PORT}/tcp" || true
  ufw allow "${PORT}/udp" || true
fi
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port="${PORT}/tcp" || true
  firewall-cmd --permanent --add-port="${PORT}/udp" || true
  firewall-cmd --reload || true
fi

sleep 1
echo
systemctl --no-pager -l status pccontroller-relay | head -n 12 || true
echo
echo "================================================================"
echo " 完成。still TODO in the Alibaba console:"
echo "   在【安全组】入方向放行  TCP ${PORT}  和  UDP ${PORT}  (源 0.0.0.0/0)"
echo "   ⚠️ 必须同时放行 UDP,否则 P2P 打洞会失败、只能走中转。"
echo
echo " 客户端「中转服务器」填:  <本机公网IP>:${PORT}"
echo "================================================================"
