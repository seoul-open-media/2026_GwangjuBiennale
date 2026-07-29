#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  mosquitto_setup.sh — 로컬 Mosquitto MQTT 브로커 설치 및 설정
#
#  역할:
#    1. Mosquitto 설치 (apt)
#    2. 비밀번호 파일 생성 (gb16_user)
#    3. mosquitto.conf 작성
#       - 포트 1883 (로컬, gb16_bridge.py 연결용)
#       - 포트 9001 (WebSocket, 대시보드 LAN 접속용)
#       - 인증 필수
#    4. systemd 재시작
#
#  실행: sudo bash mosquitto_setup.sh
#         또는: bash mosquitto_setup.sh  (sudo 자동 실행)
# ═══════════════════════════════════════════════════════════════════
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_DIR="/etc/mosquitto/conf.d"
PASSWD_FILE="/etc/mosquitto/gb16_passwd"

# .env 로드
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a; source "$SCRIPT_DIR/.env"; set +a
fi

MQTT_USER="${MQTT_USER:-gb16_user}"
MQTT_PASS="${MQTT_PASS:-}"

if [ -z "$MQTT_PASS" ]; then
    echo "[ERR] .env 에 MQTT_PASS 가 설정되지 않았습니다."
    echo "      .env 에 MQTT_PASS=YOUR_STRONG_PASSWORD 를 추가하세요."
    exit 1
fi

echo "═══════════════════════════════════════════════════"
echo " GB16 Mosquitto 브로커 설정"
echo " 사용자  : $MQTT_USER"
echo " 포트    : 1883 (TCP) / 9001 (WebSocket)"
echo "═══════════════════════════════════════════════════"

# ── 1. Mosquitto 설치 ────────────────────────────────────────────
if ! command -v mosquitto &>/dev/null; then
    echo "[..] Mosquitto 설치 중..."
    sudo apt-get update -q
    sudo apt-get install -y mosquitto mosquitto-clients
    echo "[OK] Mosquitto 설치됨"
else
    echo "[OK] Mosquitto 이미 설치됨 ($(mosquitto -v 2>&1 | head -1))"
fi

# ── 2. 비밀번호 파일 생성 ──────────────────────────────────────
echo "[..] 비밀번호 파일 생성: $PASSWD_FILE"
sudo mosquitto_passwd -b -c "$PASSWD_FILE" "$MQTT_USER" "$MQTT_PASS"
sudo chmod 600 "$PASSWD_FILE"
echo "[OK] 비밀번호 설정 완료 (user: $MQTT_USER)"

# ── 3. mosquitto.conf 작성 ─────────────────────────────────────
CONF_FILE="$CONF_DIR/gb16.conf"
echo "[..] $CONF_FILE 작성 중..."
sudo mkdir -p "$CONF_DIR"
sudo tee "$CONF_FILE" > /dev/null << EOF
# GB16 2026 광주비엔날레 Mosquitto 설정

# ── TCP (gb16_bridge.py 전용, 로컬만 허용) ──
listener 1883 127.0.0.1
allow_anonymous false
password_file $PASSWD_FILE

# ── WebSocket (대시보드 LAN 접속용) ──
listener 9001
protocol websockets
allow_anonymous false
password_file $PASSWD_FILE

# ── 로그 ──
log_type error
log_type warning
log_type information
log_dest syslog
EOF
echo "[OK] $CONF_FILE"

# ── 4. 기본 mosquitto.conf 의 anonymous listener 비활성화 ───────
# Ubuntu/Debian 기본 conf 가 포트 1883 anonymous 허용하는 경우 충돌 방지
if grep -q "^listener" /etc/mosquitto/mosquitto.conf 2>/dev/null; then
    echo "[INFO] /etc/mosquitto/mosquitto.conf 에 기존 listener 설정 있음 — 수동 확인 권장"
fi

# ── 5. Mosquitto 재시작 ─────────────────────────────────────────
echo "[..] Mosquitto 재시작..."
sudo systemctl enable mosquitto
sudo systemctl restart mosquitto
sleep 1
if systemctl is-active --quiet mosquitto; then
    echo "[OK] Mosquitto 실행 중"
else
    echo "[ERR] Mosquitto 시작 실패"
    sudo journalctl -u mosquitto -n 20 --no-pager
    exit 1
fi

# ── 6. 연결 테스트 ─────────────────────────────────────────────
echo "[..] 연결 테스트..."
if mosquitto_pub -h 127.0.0.1 -p 1883 \
       -u "$MQTT_USER" -P "$MQTT_PASS" \
       -t "gb16/test" -m "setup_ok" -q 1 2>/dev/null; then
    echo "[OK] 로컬 브로커 인증 연결 성공"
else
    echo "[WARN] 연결 테스트 실패 — 브로커 상태 확인: sudo journalctl -u mosquitto -n 20"
fi

echo ""
echo "═══════════════════════════════════════════════════"
echo " 완료"
echo " 브로커 주소:"
echo "   TCP      : mqtt://127.0.0.1:1883        (gb16_bridge.py)"
echo "   WebSocket: ws://$(hostname -I | awk '{print $1}'):9001/mqtt  (LAN 대시보드)"
echo ""
echo " .env 설정 확인:"
echo "   MQTT_BROKER=127.0.0.1"
echo "   MQTT_PORT=1883"
echo "   MQTT_USER=$MQTT_USER"
echo "   MQTT_PASS=*****"
echo "   MQTT_TLS=   (비워두면 TLS 없음 — 로컬 전용)"
echo ""
echo " 외부(인터넷) 접속은 HiveMQ Cloud 또는 ngrok TCP 터널 사용 권장"
echo "═══════════════════════════════════════════════════"
