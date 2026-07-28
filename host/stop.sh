#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  SeMA 2026 North — 종료 스크립트
# ═══════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"

echo "═══════════════════════════════════════════════════"
echo " SeMA 2026 North — 종료"
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

# systemd 서비스 중지 (sema_host, bridge)
systemctl --user stop sema-host.service  && echo "[OK] sema-host 중지" || echo "[--] sema-host 이미 중지됨"
systemctl --user stop sema-bridge  && echo "[OK] sema-bridge 중지" || echo "[--] sema-bridge 이미 중지됨"

# ngrok 터널 중지
stop_pid "ngrok"
pkill -f ngrok 2>/dev/null && echo "[OK] ngrok 종료" || true

# ── ftdi_sio 드라이버 언로드 ──────────────────────────────────────────
# kernel 6.8 xhci hard lockup 방지:
#   ftdi_sio가 로드된 상태에서 XBee를 물리적으로 재연결하면
#   xhci 슬롯 해제/재할당 중 race → silent hard lockup 발생.
#   드라이버를 미리 언로드하면 reconnect 시 xhci가 driver 없이
#   enumeration만 수행 → lockup 없음.
#   start.sh에서 modprobe ftdi_sio로 재로드.
echo "[..] ftdi_sio 언로드 (XBee 재연결 안전 모드)"
if sudo rmmod ftdi_sio 2>/dev/null; then
    echo "[OK] ftdi_sio 언로드됨 — XBee 재연결 후 start.sh 실행하세요"
else
    echo "[--] ftdi_sio 언로드 실패 (사용 중이거나 이미 없음 — 무시)"
fi

echo ""
echo "[OK] 종료 완료"
