#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  GB16 2026 광주비엔날레 — 자동 시작 설치 스크립트
#
#  수행 작업:
#    1. XBee udev 룰 설치 (/dev/xbee 심링크 + stty 설정)
#    2. systemd user 서비스 설치 및 활성화
#       gb16-host.service  gb16-bridge.service  gb16-ngrok.service
#    3. loginctl linger 활성화 (로그인 없이도 서비스 구동)
#    4. 브라우저 자동 시작 등록 (~/.config/autostart/)
#
#  실행:  bash setup_autostart.sh
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"

echo "═══════════════════════════════════════════════════"
echo " GB16 자동 시작 설치"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════════════"

# ── 1. XBee udev 룰 ─────────────────────────────────────────────────
echo ""
echo "[..] XBee udev 룰 설치"

# XBee 장치의 vendor ID 자동 감지 (FTDI: 0403 / CP210x: 10c4)
XBEE_VENDOR=""
if lsusb 2>/dev/null | grep -qi "0403:"; then
    XBEE_VENDOR="0403"
elif lsusb 2>/dev/null | grep -qi "10c4:"; then
    XBEE_VENDOR="10c4"
fi

UDEV_RULE_FILE="/etc/udev/rules.d/99-gb16-xbee.rules"
UDEV_CONTENT='# GB16 2026 광주비엔날레 — XBee USB 시리얼 장치
# FTDI (0403) 및 Silicon Labs CP210x (10c4) 자동 심링크 + stty 설정

# FTDI 기반 XBee
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", SYMLINK+="xbee", MODE:="0666", \
  RUN+="/bin/bash -c '"'"'stty -F /dev/%k raw -hupcl clocal -crtscts 2>/dev/null || true'"'"'"

# Silicon Labs CP210x 기반 XBee
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", SYMLINK+="xbee", MODE:="0666", \
  RUN+="/bin/bash -c '"'"'stty -F /dev/%k raw -hupcl clocal -crtscts 2>/dev/null || true'"'"'"
'

sudo tee "$UDEV_RULE_FILE" > /dev/null << 'EOF'
# GB16 2026 광주비엔날레 — XBee USB 시리얼 장치
# FTDI (0403) 및 Silicon Labs CP210x (10c4) 자동 심링크 + stty 설정

# FTDI 기반 XBee
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", SYMLINK+="xbee", MODE:="0666"

# Silicon Labs CP210x 기반 XBee
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", SYMLINK+="xbee", MODE:="0666"
EOF

sudo udevadm control --reload-rules && sudo udevadm trigger 2>/dev/null || true
echo "[OK] udev 룰 설치됨: $UDEV_RULE_FILE"
if [ -n "$XBEE_VENDOR" ]; then
    echo "     감지된 XBee vendor ID: $XBEE_VENDOR"
fi

# ── 2. systemd user 서비스 설치 ─────────────────────────────────────
echo ""
echo "[..] systemd user 서비스 설치"
mkdir -p "$SYSTEMD_USER_DIR"

for svc in gb16-host.service gb16-bridge.service gb16-ngrok.service; do
    src="$SCRIPT_DIR/systemd/$svc"
    dst="$SYSTEMD_USER_DIR/$svc"
    if [ -f "$src" ]; then
        cp -f "$src" "$dst"
        echo "[OK] $svc → $dst"
    else
        echo "[WARN] $src 없음 — 건너뜀"
    fi
done

systemctl --user daemon-reload
echo "[OK] daemon-reload 완료"

# ── 3. 서비스 활성화 ────────────────────────────────────────────────
echo ""
echo "[..] 서비스 활성화 (부팅 시 자동 시작)"
systemctl --user enable gb16-host.service
systemctl --user enable gb16-bridge.service
systemctl --user enable gb16-ngrok.service 2>/dev/null || \
    echo "[WARN] gb16-ngrok.service 활성화 실패 (ngrok 설치 확인 필요)"
echo "[OK] 서비스 활성화 완료"

# ── 4. linger 활성화 (로그인 없이 user 서비스 구동) ─────────────────
echo ""
echo "[..] loginctl linger 활성화"
loginctl enable-linger "$USER"
echo "[OK] linger 활성화됨 (재부팅 후 자동 시작)"

# ── 5. 브라우저 자동 시작 등록 ──────────────────────────────────────
echo ""
echo "[..] 브라우저 자동 시작 등록"
mkdir -p "$AUTOSTART_DIR"

# 컨트롤 페이지 URL (로컬 서버)
CONTROL_URL="http://localhost:8182/gb16_control.html"

# 지연 실행 스크립트 (서비스가 준비될 때까지 대기)
LAUNCHER_SCRIPT="$SCRIPT_DIR/open_control.sh"
cat > "$LAUNCHER_SCRIPT" << LAUNCHER
#!/usr/bin/env bash
# GB16 컨트롤 페이지 자동 열기 (서비스 준비 대기)
CONTROL_URL="$CONTROL_URL"
MAX_WAIT=60

echo "[..] GB16 컨트롤 페이지 대기 중..."
for i in \$(seq 1 \$MAX_WAIT); do
    if wget -q --spider "\$CONTROL_URL" 2>/dev/null; then
        break
    fi
    sleep 1
done

# Chromium/Chrome 우선, 없으면 Firefox
CHROME_FLAGS="--disable-gpu --disable-software-rasterizer --disable-infobars \
    --disable-session-crashed-bubble \
    --no-first-run \
    --no-default-browser-check \
    --noerrdialogs \
    --disable-translate \
    --user-data-dir=/tmp/gb16-chrome-profile"

if command -v google-chrome &>/dev/null; then
    exec google-chrome \$CHROME_FLAGS "\$CONTROL_URL"
elif command -v chromium-browser &>/dev/null; then
    exec chromium-browser \$CHROME_FLAGS "\$CONTROL_URL"
elif command -v firefox &>/dev/null; then
    exec firefox "\$CONTROL_URL"
fi
LAUNCHER
chmod +x "$LAUNCHER_SCRIPT"

# XDG autostart 데스크탑 항목
cat > "$AUTOSTART_DIR/gb16-control.desktop" << DESKTOP
[Desktop Entry]
Type=Application
Name=GB16 컨트롤 페이지
Comment=GB16 2026 광주비엔날레 컨트롤 패널 자동 실행
Exec=$LAUNCHER_SCRIPT
Terminal=false
Hidden=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
DESKTOP

echo "[OK] 브라우저 자동 시작 등록됨: $AUTOSTART_DIR/gb16-control.desktop"
echo "     열릴 URL: $CONTROL_URL"

# ── 완료 ────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════"
echo " 설치 완료!"
echo ""
echo " 부팅 시 자동 시작 목록:"
echo "   - gb16-host.service   (XBee 시리얼 게이트웨이)"
echo "   - gb16-bridge.service (MQTT 브리지)"
echo "   - gb16-ngrok.service  (ngrok 터널)"
echo "   - 브라우저: $CONTROL_URL"
echo ""
echo " 서비스 상태 확인:"
echo "   systemctl --user status gb16-host gb16-bridge gb16-ngrok"
echo ""
echo " 지금 바로 시작하려면:"
echo "   systemctl --user start gb16-host gb16-bridge gb16-ngrok"
echo "═══════════════════════════════════════════════════"
