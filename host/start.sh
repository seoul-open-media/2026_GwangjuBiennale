#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  SeMA 2026 North — 시작 스크립트
#  실행 순서:
#    1. USB 시리얼 설정   (stty /dev/ttyUSB0 raw)
#    2. 시리얼 게이트웨이  (sema_host.py)
#    3. MQTT 브리지       (bridge.py, systemd)
#    4. 관객 감지 데몬    (audience_detector.py, systemd)
#    5. ngrok 터널
#    ※ 대시보드/제어 패널 Chrome 창은 별도 systemd 서비스로 관리
#       (sema-dashboard.service / sema-control.service)
#
#  종료: ./stop.sh
# ═══════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$SCRIPT_DIR/../tools/mqtt_monitor"
DASHBOARD="$TOOLS_DIR/dashboard.html"
LOG_DIR="$SCRIPT_DIR/logs"

mkdir -p "$LOG_DIR"

echo "═══════════════════════════════════════════════════"
echo " SeMA 2026 North — 시작"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════════════"

# ── 1. ftdi_sio 재로드 + USB 시리얼 설정 ────────────────────────────
# stop.sh가 ftdi_sio를 언로드했으므로 재로드 후 /dev/xbee 대기
echo "[..] ftdi_sio 로드"
sudo modprobe ftdi_sio 2>/dev/null && echo "[OK] ftdi_sio 로드됨" || echo "[--] ftdi_sio 이미 로드됨"
udevadm settle --timeout=3 2>/dev/null || true   # udev symlink(/dev/xbee) 재생성 대기

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
    # raw + HUPCL 해제 + CLOCAL: 닫을 때 DTR 강하 및 break 전송 억제 (커널 크래시 방지)
    stty -F /dev/xbee raw -hupcl clocal -crtscts
    echo "[OK] stty /dev/xbee raw -hupcl clocal -crtscts"
fi

# ── 2. 시리얼 게이트웨이 (sema_host.py, systemd) ──────────────────
echo "[..] sema-host 서비스 시작"
systemctl --user start sema-host.service && echo "[OK] sema-host.service 시작됨  로그: logs/sema_host.log" || echo "[ERR] sema-host.service 시작 실패"

# ── 3. bridge.py (systemd 서비스 — 크래시 시 자동 재시작) ─────────────
echo "[..] sema-bridge 서비스 시작"
systemctl --user restart sema-bridge
echo "[OK] sema-bridge 시작  로그: tools/mqtt_monitor/bridge.log"

# ── 4. 관객 감지 데몬 (systemd 서비스 — 크래시 시 자동 재시작) ────────────
echo "[..] sema-audience 서비스 시작"
systemctl --user restart sema-audience
echo "[OK] sema-audience 시작  로그: tools/mqtt_monitor/audience.log"

# ── 5. ngrok 터널 (외부 접속용 고정 URL) ────────────────────────
pkill -f ngrok 2>/dev/null; sleep 1
ngrok http --domain=android-handclap-ditto.ngrok-free.dev 8181 \
    >> "$LOG_DIR/ngrok.log" 2>&1 &
NGROK_PID=$!
echo $NGROK_PID > "$LOG_DIR/ngrok.pid"
echo "[OK] ngrok 시작 (PID $NGROK_PID)"
echo "     외부 URL: https://android-handclap-ditto.ngrok-free.dev/dashboard.html"

# ── 대시보드 / 제어 패널은 별도 systemd 서비스가 담당 ────────────────
echo "[INFO] 대시보드/제어 패널 → sema-dashboard / sema-control 서비스 참조"

echo ""
echo "═══════════════════════════════════════════════════"
echo " 시작 완료  |  종료: $SCRIPT_DIR/stop.sh"
echo " 외부 URL : https://android-handclap-ditto.ngrok-free.dev/dashboard.html"
echo "═══════════════════════════════════════════════════"
