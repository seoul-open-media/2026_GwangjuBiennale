#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  GB16 2026 광주비엔날레 — 시작 스크립트
#  실행 순서:
#    1. USB 시리얼 설정   (stty /dev/xbee raw)
#    2. 시리얼 게이트웨이  (gb16-host.service)
#    3. MQTT 브리지       (gb16-bridge.service)
#    4. ngrok 터널
#
#  종료: ./stop.sh
#
#  최초 실행 전 systemd 서비스 등록 필요:
#    cp systemd/gb16-host.service   ~/.config/systemd/user/
#    cp systemd/gb16-bridge.service ~/.config/systemd/user/
#    systemctl --user daemon-reload
# ═══════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"

mkdir -p "$LOG_DIR"

echo "═══════════════════════════════════════════════════"
echo " GB16 2026 광주비엔날레 — 시작"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════════════"

# ── 1. ftdi_sio 재로드 + USB 시리얼 설정 ────────────────────────────
echo "[..] ftdi_sio 로드"
sudo modprobe ftdi_sio 2>/dev/null && echo "[OK] ftdi_sio 로드됨" || echo "[--] ftdi_sio 이미 로드됨"
udevadm settle --timeout=3 2>/dev/null || true

echo "[..] /dev/xbee 대기 중..."
for i in $(seq 1 20); do
    if [ -e /dev/xbee ]; then
        echo "[OK] /dev/xbee 감지됨"
        break
    fi
    sleep 0.5
done
if [ ! -e /dev/xbee ]; then
    echo "[WARN] /dev/xbee 없음 — XBee가 연결되어 있는지 확인하세요"
fi

if [ -e /dev/xbee ]; then
    stty -F /dev/xbee raw -hupcl clocal -crtscts
    echo "[OK] stty /dev/xbee raw -hupcl clocal -crtscts"
fi

# ── 2. 시리얼 게이트웨이 (gb16_host.py, systemd) ──────────────────
echo "[..] gb16-host 서비스 시작"
systemctl --user start gb16-host.service && echo "[OK] gb16-host.service 시작됨  로그: logs/gb16_host.log" || echo "[ERR] gb16-host.service 시작 실패"

# ── 3. gb16_bridge.py (systemd 서비스) ─────────────────────────────
echo "[..] gb16-bridge 서비스 시작"
systemctl --user restart gb16-bridge.service
echo "[OK] gb16-bridge 시작  로그: logs/gb16_bridge.log"

# ── 4. ngrok 터널 (외부 접속용) ─────────────────────────────────────
# .env 에서 NGROK_DOMAIN 로드
NGROK_DOMAIN="${NGROK_DOMAIN:-}"
if [ -f "$SCRIPT_DIR/.env" ]; then
    NGROK_DOMAIN_ENV=$(grep -E '^NGROK_DOMAIN=' "$SCRIPT_DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")
    [ -n "$NGROK_DOMAIN_ENV" ] && NGROK_DOMAIN="$NGROK_DOMAIN_ENV"
fi

STATS_PORT="${STATS_PORT:-8182}"

if [ -n "$NGROK_DOMAIN" ] && [ "$NGROK_DOMAIN" != "YOUR_NGROK_DOMAIN_HERE" ]; then
    pkill -f "ngrok.*$STATS_PORT" 2>/dev/null; sleep 1
    ngrok http --domain="$NGROK_DOMAIN" "$STATS_PORT" \
        >> "$LOG_DIR/ngrok.log" 2>&1 &
    NGROK_PID=$!
    echo $NGROK_PID > "$LOG_DIR/ngrok.pid"
    echo "[OK] ngrok 시작 (PID $NGROK_PID)"
    echo "     컨트롤 URL: https://${NGROK_DOMAIN}/gb16_control.html"
else
    echo "[--] ngrok 도메인 미설정 — .env 에 NGROK_DOMAIN=... 추가하세요"
fi

echo ""
echo "═══════════════════════════════════════════════════"
echo " 시작 완료  |  종료: $SCRIPT_DIR/stop.sh"
echo " 로컬 URL  : http://localhost:${STATS_PORT}/gb16_control.html"
[ -n "$NGROK_DOMAIN" ] && echo " 외부 URL  : https://${NGROK_DOMAIN}/gb16_control.html"
echo "═══════════════════════════════════════════════════"
