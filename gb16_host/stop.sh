#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  GB16 2026 광주비엔날레 — 종료 스크립트
# ═══════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"

echo "═══════════════════════════════════════════════════"
echo " GB16 2026 광주비엔날레 — 종료"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════════════"

stop_pid() {
    local name="$1"
    local pid_file="$LOG_DIR/$name.pid"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            echo "[OK] $name 종료 (PID $pid)"
        else
            echo "[--] $name 이미 종료됨 (PID $pid)"
        fi
        rm -f "$pid_file"
    else
        echo "[--] $name PID 파일 없음"
    fi
}

# systemd 서비스 중지
systemctl --user stop gb16-host.service   && echo "[OK] gb16-host 중지"   || echo "[--] gb16-host 이미 중지됨"
systemctl --user stop gb16-bridge.service && echo "[OK] gb16-bridge 중지" || echo "[--] gb16-bridge 이미 중지됨"

# ngrok 터널 중지
stop_pid "ngrok"
pkill -f "ngrok" 2>/dev/null && echo "[OK] ngrok 종료" || true

# ── ftdi_sio 드라이버 언로드 ──────────────────────────────────────────
echo "[..] ftdi_sio 언로드 (XBee 재연결 안전 모드)"
if sudo rmmod ftdi_sio 2>/dev/null; then
    echo "[OK] ftdi_sio 언로드됨 — XBee 재연결 후 start.sh 실행하세요"
else
    echo "[--] ftdi_sio 언로드 실패 (사용 중이거나 이미 없음 — 무시)"
fi

echo ""
echo "[OK] 종료 완료"
