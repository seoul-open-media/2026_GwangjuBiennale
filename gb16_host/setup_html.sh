#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  setup_html.sh — sema HTML을 GB16용으로 복사 + 자동 변환
#
#  실행 전 .env 에 다음 항목을 설정하세요:
#    GB16_HOST_IP=192.168.x.x      (GB16 서버 로컬 IP)
#    NGROK_DOMAIN=YOUR_DOMAIN.ngrok-free.app
#    STATS_API_PORT=8182           (기본값)
#    MQTT_LOCAL_PORT=9001          (로컬 Mosquitto WebSocket 포트, 기본값)
#
#  실행: bash setup_html.sh
# ═══════════════════════════════════════════════════════════════════
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/../tools/mqtt_monitor"

# .env 로드
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

GB16_HOST_IP="${GB16_HOST_IP:-YOUR_GB16_IP_HERE}"
NGROK_DOMAIN="${NGROK_DOMAIN:-YOUR_NGROK_DOMAIN_HERE}"
STATS_API_PORT="${STATS_API_PORT:-8182}"
MQTT_LOCAL_PORT="${MQTT_LOCAL_PORT:-9001}"
MQTT_BROKER_WSS="${MQTT_BROKER_WSS:-wss://broker.hivemq.com:8884/mqtt}"
MQTT_BROKER_WS="${MQTT_BROKER_WS:-ws://YOUR_GB16_IP:9001/mqtt}"

SEMA_IP="192.168.50.51"
SEMA_STATS_PORT="8181"
SEMA_NGROK="android-handclap-ditto.ngrok-free.dev"
SEMA_BROKER_WSS="wss://broker.hivemq.com:8884/mqtt"
SEMA_BROKER_WS_PATTERN="ws://192\.168\.50\.51:9001/mqtt"

echo "═══════════════════════════════════════════════════"
echo " GB16 HTML 셋업"
echo " 소스: $SRC_DIR"
echo " 대상: $SCRIPT_DIR"
echo " GB16 IP  : $GB16_HOST_IP"
echo " ngrok    : $NGROK_DOMAIN"
echo " 포트     : $STATS_API_PORT"
echo "═══════════════════════════════════════════════════"

if [ ! -f "$SRC_DIR/sema_control.html" ] || [ ! -f "$SRC_DIR/dashboard.html" ]; then
    echo "[ERR] 소스 HTML 없음: $SRC_DIR"
    exit 1
fi

# ── 1. dashboard.html 변환 ───────────────────────────────────────
echo "[..] dashboard.html 변환 중..."
cp "$SRC_DIR/dashboard.html" "$SCRIPT_DIR/dashboard.html"
sed -i \
    -e "s|sma/robot/|gb16/robot/|g" \
    -e "s|sema_north|gb16_user|g" \
    -e "s|sema_ctrl_|gb16_ctrl_|g" \
    -e "s|192\.168\.50\.51|$GB16_HOST_IP|g" \
    -e "s|:${SEMA_STATS_PORT}/|:${STATS_API_PORT}/|g" \
    -e "s|${SEMA_NGROK}|${NGROK_DOMAIN}|g" \
    -e "s|wss://broker\.hivemq\.com:8884/mqtt|${MQTT_BROKER_WSS}|g" \
    -e "s|ws://192\.168\.50\.51:9001/mqtt|${MQTT_BROKER_WS}|g" \
    "$SCRIPT_DIR/dashboard.html"

# GB16 전용 추가 변환 (dashboard)
python3 - "$SCRIPT_DIR/dashboard.html" <<'PYEOF'
import sys, re, pathlib

f = pathlib.Path(sys.argv[1])
t = f.read_text(encoding='utf-8')

# 1. ROBOT_IDS 확장 (1~34)
t = re.sub(
    r'const ROBOT_IDS = \[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18\];',
    'const ROBOT_IDS = [' + ', '.join(str(i) for i in range(1, 35)) + '];',
    t
)

# 2. isGP 제거 (모두 IF)
t = t.replace('const isGP = r.id <= 12;', 'const isGP = false;')

# 3. PRESET_NAMES 항상 IF 버전 사용
t = t.replace(
    'const PRESET_NAMES = r.id >= 13 ? PRESET_NAMES_IF : PRESET_NAMES_GP;',
    'const PRESET_NAMES = PRESET_NAMES_IF;'
)

# 4. stats isIF 수정
t = t.replace('const isIF  = r.robot_id >= 13;', 'const isIF  = r.robot_id >= 1;')

# 5. 헤더 범례 텍스트 업데이트
t = re.sub(
    r'R01[^=]+=\s*Golden Petals[^<]+R13[^=]+=\s*Indeterministic Float',
    'R01–R30 = Indeterministic Float \u00a0|\u00a0 R31–R34 = IF Ceiling',
    t
)

# 6. 그룹 레이블 교체 (황금빛 꽃 행 포함 전부 교체)
old_groups = (
    "{ label: '황금빛 꽃  [R1\u2013R8]',    cls: 'gf',  ids: [1,2,3,4,5,6,7,8] },\n"
    "  { label: '황금빛 꽃잎 1  [R9\u2013R10]', cls: 'gp1', ids: [9,10] },\n"
    "  { label: '황금빛 꽃잎 2  [R11\u2013R12]',cls: 'gp2', ids: [11,12] },\n"
    "  { label: '비결정적 유영  [R13\u2013R18]', cls: 'if',  ids: [13,14,15,16,17,18] },"
)
new_groups = (
    "{ label: '비결정적 유영 A  [R01\u2013R15]', cls: 'if',  ids: [" +
    ','.join(str(i) for i in range(1, 16)) + "] },\n" +
    "  { label: '비결정적 유영 B  [R16\u2013R30]', cls: 'if',  ids: [" +
    ','.join(str(i) for i in range(16, 31)) + "] },\n" +
    "  { label: 'IF Ceiling  [R31\u2013R34]',      cls: 'if_ceil', ids: [31,32,33,34] },"
)
if old_groups in t:
    t = t.replace(old_groups, new_groups)
else:
    # 원문 형식이 다를 경우 정규식으로 대체
    t = re.sub(
        r"\{ label: '황금빛 꽃  \[R1.*?\{ label: '황금빛 꽃잎 1.*?ids: \[13[^\]]*\] \},",
        new_groups,
        t, flags=re.DOTALL
    )

# 7. GP 프리셋 참조표 제거 (IF 참조표만 유지), 헤더 텍스트 수정
t = re.sub(
    r'<!-- 프리셋 참조표.*?<h3 style="margin-top:16px">🎬 프리셋 참조 \(Indeterministic Float\)</h3>',
    '<!-- 프리셋 참조표 -->\n<div id="preset-panel">\n  <h3>🎬 프리셋 참조 (Indeterministic Float / IF Ceiling)</h3>',
    t, flags=re.DOTALL
)

# 8. shortName에서 Golden Petals 제거
t = t.replace(
    "r.name.replace('Golden Petals ', 'GP ').replace('Indet. Float ', 'IF ')",
    "r.name.replace('Indet. Float ', 'IF ').replace('IF Ceiling ', 'IFC ')"
)

f.write_text(t, encoding='utf-8')
print("[OK] dashboard.html GB16 변환 완료")
PYEOF
echo "[OK] dashboard.html"

# ── 2. gb16_control.html 변환 ────────────────────────────────────
echo "[..] gb16_control.html 변환 중..."
cp "$SRC_DIR/sema_control.html" "$SCRIPT_DIR/gb16_control.html"
sed -i \
    -e "s|sma/robot/|gb16/robot/|g" \
    -e "s|sma/cue|gb16/cue|g" \
    -e "s|sma/power/group/|gb16/power/group/|g" \
    -e "s|sma/light/cmd|gb16/light/cmd|g" \
    -e "s|sma/light/status|gb16/light/status|g" \
    -e "s|sema_north|gb16_user|g" \
    -e "s|sema_ctrl_|gb16_ctrl_|g" \
    -e "s|sema_control\.html|gb16_control.html|g" \
    -e "s|SeMA 2026 North|GB16 2026 광주비엔날레|g" \
    -e "s|192\.168\.50\.51|$GB16_HOST_IP|g" \
    -e "s|:${SEMA_STATS_PORT}/|:${STATS_API_PORT}/|g" \
    -e "s|${SEMA_NGROK}|${NGROK_DOMAIN}|g" \
    -e "s|wss://broker\.hivemq\.com:8884/mqtt|${MQTT_BROKER_WSS}|g" \
    -e "s|ws://192\.168\.50\.51:9001/mqtt|${MQTT_BROKER_WS}|g" \
    "$SCRIPT_DIR/gb16_control.html"

# GB16 전용 추가 변환 (gb16_control)
python3 - "$SCRIPT_DIR/gb16_control.html" <<'PYEOF'
import sys, re, pathlib

IF_IDS_A   = list(range(1, 16))    # 비결정적 유영 A  R01–R15
IF_IDS_B   = list(range(16, 31))   # 비결정적 유영 B  R16–R30
IF_CEIL    = list(range(31, 35))   # IF Ceiling        R31–R34
ALL_IDS    = IF_IDS_A + IF_IDS_B + IF_CEIL

def ids_js(lst): return '[' + ','.join(str(i) for i in lst) + ']'

f = pathlib.Path(sys.argv[1])
t = f.read_text(encoding='utf-8')

# 1. ID 상수 교체
t = re.sub(
    r'const GF_IDS\s*=.*?\n.*?const GP1_IDS\s*=.*?\n.*?const GP2_IDS\s*=.*?\n'
    r'.*?const GP_IDS\s*=.*?\n.*?const IF_IDS\s*=.*?\n.*?const ALL_IDS\s*=.*?;',
    (f"const IF_IDS_A   = {ids_js(IF_IDS_A)};  // 비결정적 유영 A\n"
     f"const IF_IDS_B   = {ids_js(IF_IDS_B)};  // 비결정적 유영 B\n"
     f"const IF_CEIL    = {ids_js(IF_CEIL)};    // IF Ceiling\n"
     f"const GP_IDS     = [];                    // 미사용\n"
     f"const IF_IDS     = {ids_js(ALL_IDS)};    // 전체 IF\n"
     f"const ALL_IDS    = IF_IDS;"),
    t, flags=re.DOTALL
)

# 2. GROUP_ROBOT_IDS 교체
t = re.sub(
    r'const GROUP_ROBOT_IDS\s*=\s*\{[^}]+\};',
    (f"const GROUP_ROBOT_IDS = {{ "
     f"ifa: IF_IDS_A, ifb: IF_IDS_B, if: IF_IDS, ceil: IF_CEIL }};"),
    t
)

# 3. 그룹 버튼 HTML 교체
t = re.sub(
    r"<button class=\"gbtn\" onclick=\"selectGroup\('all'\)\">전체 18</button>.*?"
    r"<button class=\"gbtn\" onclick=\"selectGroup\('none'\)\">해제</button>",
    ("<button class=\"gbtn\" onclick=\"selectGroup('all')\">전체 34</button>\n"
     "        <button class=\"gbtn\" onclick=\"selectGroup('ifa')\">유영 A (1-15)</button>\n"
     "        <button class=\"gbtn\" onclick=\"selectGroup('ifb')\">유영 B (16-30)</button>\n"
     "        <button class=\"gbtn\" onclick=\"selectGroup('ceil')\">천장 (31-34)</button>\n"
     "        <button class=\"gbtn\" onclick=\"selectGroup('none')\">해제</button>"),
    t, flags=re.DOTALL
)

# 4. isGP 항상 false
t = t.replace('const isGP    = GP_IDS.includes(r.id);', 'const isGP    = false;')

# 5. 필터 드롭다운 GP 옵션 교체
t = re.sub(
    r"<option value=\"ALL\"[^>]*>전체 \(GP\+IF\)</option>\s*"
    r"<option value=\"GP\">GP만 \(1-12\)</option>",
    "<option value=\"ALL\" selected>전체 (IF)</option>",
    t
)

# 6. 그룹 레이블 교체
t = re.sub(
    r'<div class="grid-label"><span>황금빛 꽃  \(R01 – R08\)</span>.*?</label></div>',
    '<div class="grid-label"><span>비결정적 유영 A  (R01 – R15)</span>'
    '<label class="ar-cb" id="ar-label-ifa"><input type="checkbox" id="ar-ifa" '
    'onchange="toggleAutoReset(\'ifa\',this)"> 자동리셋</label></div>',
    t, flags=re.DOTALL
)
# gp1/gp2 행 제거, if 행 교체
t = re.sub(
    r'<div class="grid-label"><span>황금빛 꽃잎1.*?</label></div>\s*'
    r'<div class="robot-grid" id="grid-gp1"></div>\s*'
    r'<div class="grid-label"><span>황금빛 꽃잎2.*?</label></div>\s*'
    r'<div class="robot-grid" id="grid-gp2"></div>\s*'
    r'<div class="grid-label"><span>비결정적 유영  \(R13 – R18\)</span>.*?</label></div>',
    ('<div class="robot-grid" id="grid-ifa"></div>\n'
     '      <div class="grid-label"><span>비결정적 유영 B  (R16 – R30)</span>'
     '<label class="ar-cb" id="ar-label-ifb"><input type="checkbox" id="ar-ifb" '
     'onchange="toggleAutoReset(\'ifb\',this)"> 자동리셋</label></div>'),
    t, flags=re.DOTALL
)
# if 그리드 id 및 ceiling 추가
t = re.sub(
    r'<div class="robot-grid" id="grid-if"></div>',
    ('<div class="robot-grid" id="grid-ifb"></div>\n'
     '      <div class="grid-label"><span>IF Ceiling  (R31 – R34)</span>'
     '<label class="ar-cb" id="ar-label-ceil"><input type="checkbox" id="ar-ceil" '
     'onchange="toggleAutoReset(\'ceil\',this)"> 자동리셋</label></div>\n'
     '      <div class="robot-grid" id="grid-ceil"></div>'),
    t
)

# 7. ALL_IDS 초기화 루프 (forEach) 는 유지 — IF_IDS로 이미 = ALL_IDS

# 8. 매뉴얼 텍스트 업데이트
t = t.replace('GP R01–R12</span><span class="man-val">황금빛 꽃잎 (초록 테두리)',
              'IF R01–R30</span><span class="man-val">비결정적 유영 (보라 테두리')
t = t.replace('IF R13–R18</span><span class="man-val">비결정적 유영 (보라 테두리)',
              'IF Ceiling R31–R34</span><span class="man-val">IF Ceiling (파랑 테두리')

# 9. GP 프리셋 레이블·버튼 섹션 제거
t = re.sub(
    r'<div class="preset-label">GP \(Golden Petals\)</div>\s*<div class="preset-grid"[^>]*>.*?</div>',
    '',
    t, flags=re.DOTALL
)

# 10. renderAll() — 존재하지 않는 grid ID 사용 수정
t = t.replace(
    "  document.getElementById('grid-gf').innerHTML   = GF_IDS.map(id  => renderCard(robots[id])).join('');\n"
    "  document.getElementById('grid-gp1').innerHTML  = GP1_IDS.map(id => renderCard(robots[id])).join('');\n"
    "  document.getElementById('grid-gp2').innerHTML  = GP2_IDS.map(id => renderCard(robots[id])).join('');\n"
    "  document.getElementById('grid-if').innerHTML   = IF_IDS.map(id  => renderCard(robots[id])).join('');",
    "  document.getElementById('grid-ifa').innerHTML  = IF_IDS_A.map(id => renderCard(robots[id])).join('');\n"
    "  document.getElementById('grid-ifb').innerHTML  = IF_IDS_B.map(id => renderCard(robots[id])).join('');\n"
    "  document.getElementById('grid-ceil').innerHTML = IF_CEIL.map(id  => renderCard(robots[id])).join('');",
)

# 11. 잔여 grid-gf div 제거 (GF_IDS = [] 이므로 불필요)
t = t.replace('      <div class="robot-grid" id="grid-gf"></div>\n', '')

# 12. 🔐 인증 버튼 제거
t = re.sub(r'  <button id="mqtt-cfg-btn"[^\n]+\n', '', t)

# 13. 📊 통계 버튼 제거
t = re.sub(r'  <button onclick="openStats\(\)"[^\n]+\n', '', t)

# 14. 인증 CSS 제거: #mqtt-cfg-btn + #mqtt-cfg-btn:hover
t = re.sub(r'#mqtt-cfg-btn \{[^}]+\}\n#mqtt-cfg-btn:hover \{[^}]+\}\n', '', t)

# 15. 인증 CSS 패널 블록 제거 (/* ── MQTT 인증 설정 패널 */ ~ .cfg-clear 마지막 규칙)
t = re.sub(r'/\* ── MQTT 인증 설정 패널.*?#mqtt-cfg-panel \.cfg-clear \{.*?\}\n\n',
           '', t, flags=re.DOTALL)

# 16. 인증 HTML 패널 div 제거
t = re.sub(r'\n<!-- ── MQTT 인증 설정 패널.*?-->\n<div id="mqtt-cfg-panel">.*?</div>\n',
           '\n', t, flags=re.DOTALL)

# 17. 인증 JS 변수 (localStorage) 제거 → 하드코딩 자격증명 상수로 교체
t = re.sub(
    r'// ── MQTT 인증 \(localStorage[^\n]*\n'
    r'let mqttBrokerOverride[^\n]*\n'
    r'let mqttUser[^\n]*\n'
    r'let mqttPass[^\n]*\n',
    "const mqttBrokerOverride = '';\n"
    "const mqttUser = 'gb16_host';\n"
    "const mqttPass = 'som123';\n",
    t
)

# 18. toggleAccessPanel 내 mqtt-cfg-panel 숨기기 코드 제거
t = t.replace(
    "  if (!visible) document.getElementById('mqtt-cfg-panel').style.display = 'none';\n",
    ''
)

# 19. 인증 관련 JS 함수 3개 제거
for fn in ('toggleMqttCfg', 'applyMqttCfg', 'clearMqttCfg'):
    t = re.sub(r'\nfunction ' + fn + r'\(\) \{.*?\n}\n', '\n', t, flags=re.DOTALL)

# 20. 통계 모달 HTML + chart.js + 통계 JS 스크립트 블록 전체 제거
t = re.sub(r'\n\n<!-- ── 통계 모달 ──[^\n]*\n.*?</body>', '\n</body>', t, flags=re.DOTALL)

# 21. GP 파라미터 탭 버튼 제거
t = re.sub(r'        <div class="ptab active" id="tab-gp"[^\n]+\n', '', t)

# 22. params-gp 패널 제거 (주석 포함)
t = re.sub(r'      <!-- GP 파라미터 -->.*?      <!-- IF 파라미터 -->', '      <!-- IF 파라미터 -->', t, flags=re.DOTALL)

# 23. IF 탭 기본 활성화 (active 클래스 추가 + display:none 제거)
t = t.replace('<div class="ptab" id="tab-if" onclick="switchParamTab(\'if\')">IF</div>',
              '<div class="ptab active" id="tab-if" onclick="switchParamTab(\'if\')">IF</div>')
t = t.replace('<div id="params-if" style="display:none">', '<div id="params-if">')

# 24. paramTab 기본값 수정
t = t.replace("let paramTab   = 'gp';", "let paramTab   = 'if';")

# 25. switchParamTab 에서 params-gp / tab-gp 참조 제거
t = re.sub(r"  document\.getElementById\('params-gp'\)\.style\.display[^\n]+\n", '', t)
t = re.sub(r"  document\.getElementById\('tab-gp'\)\.classList[^\n]+\n", '', t)

# 26. FAN_ON 버튼 GP 조건 제거
t = t.replace(
    "sendSelected('FAN_ON speed='+(paramTab==='gp'?gpFanVal():ifFanVal()))",
    "sendSelected('FAN_ON speed='+ifFanVal())"
)

# 27. 전원 제어 GROUP_NAMES JS 업데이트 (1~6 → 1~4, GB16 이름)
t = re.sub(
    r"const GROUP_NAMES = \{[^}]+\};",
    "const GROUP_NAMES = {\n"
    "  1: '비결정적 유영 A',\n"
    "  2: '비결정적 유영 B',\n"
    "  3: 'IF Ceiling',\n"
    "  4: '기타',\n"
    "};",
    t
)

# 28. 전원 제어 HTML 그리드: 6행 → 4행 (GB16 이름)
new_pwr_grid = (
    '      <div class="pwr-grid">\n'
    '        <div class="pwr-row">\n'
    '          <span class="pwr-id">1</span><span class="pwr-name">비결정적 유영 A</span>\n'
    '          <div class="pwr-btns">\n'
    '            <button class="pbr btn-on"  onclick="sendPower(1,\'on\')">ON</button>\n'
    '            <button class="pbr btn-off" onclick="sendPower(1,\'off\')">OFF</button>\n'
    '            <button class="pbr btn-rst" onclick="sendPower(1,\'reset\')">↺</button>\n'
    '          </div>\n'
    '        </div>\n'
    '        <div class="pwr-row">\n'
    '          <span class="pwr-id">2</span><span class="pwr-name">비결정적 유영 B</span>\n'
    '          <div class="pwr-btns">\n'
    '            <button class="pbr btn-on"  onclick="sendPower(2,\'on\')">ON</button>\n'
    '            <button class="pbr btn-off" onclick="sendPower(2,\'off\')">OFF</button>\n'
    '            <button class="pbr btn-rst" onclick="sendPower(2,\'reset\')">↺</button>\n'
    '          </div>\n'
    '        </div>\n'
    '        <div class="pwr-row">\n'
    '          <span class="pwr-id">3</span><span class="pwr-name">IF Ceiling</span>\n'
    '          <div class="pwr-btns">\n'
    '            <button class="pbr btn-on"  onclick="sendPower(3,\'on\')">ON</button>\n'
    '            <button class="pbr btn-off" onclick="sendPower(3,\'off\')">OFF</button>\n'
    '            <button class="pbr btn-rst" onclick="sendPower(3,\'reset\')">↺</button>\n'
    '          </div>\n'
    '        </div>\n'
    '        <div class="pwr-row">\n'
    '          <span class="pwr-id">4</span><span class="pwr-name">기타</span>\n'
    '          <div class="pwr-btns">\n'
    '            <button class="pbr btn-on"  onclick="sendPower(4,\'on\')">ON</button>\n'
    '            <button class="pbr btn-off" onclick="sendPower(4,\'off\')">OFF</button>\n'
    '            <button class="pbr btn-rst" onclick="sendPower(4,\'reset\')">↺</button>\n'
    '          </div>\n'
    '        </div>\n'
    '      </div>\n'  # </div> for pwr-grid
)
t = re.sub(
    r'      <div class="pwr-grid">.*?      </div>\n      <div id="pwr-log">',
    new_pwr_grid + '      <div id="pwr-log">',
    t, flags=re.DOTALL
)

# 29. 매뉴얼 섹션 전원 그룹 이름 업데이트 (6행 → 4행)
t = re.sub(
    r'        <div class="man-row"><span class="man-key info">1</span><span class="man-val">황금칩 꽃</span></div>\n'
    r'        <div class="man-row"><span class="man-key info">2</span><span class="man-val">황금빛 꽃잎 1</span></div>\n'
    r'        <div class="man-row"><span class="man-key info">3</span><span class="man-val">황금빛 꽃잎 2</span></div>\n'
    r'        <div class="man-row"><span class="man-key info">4</span><span class="man-val">비결정적 유영</span></div>\n'
    r'        <div class="man-row"><span class="man-key info">5</span><span class="man-val">아해들</span></div>\n'
    r'        <div class="man-row"><span class="man-key info">6</span><span class="man-val">황금빛 꽃 조명</span></div>',
    '        <div class="man-row"><span class="man-key info">1</span><span class="man-val">비결정적 유영 A</span></div>\n'
    '        <div class="man-row"><span class="man-key info">2</span><span class="man-val">비결정적 유영 B</span></div>\n'
    '        <div class="man-row"><span class="man-key info">3</span><span class="man-val">IF Ceiling</span></div>\n'
    '        <div class="man-row"><span class="man-key info">4</span><span class="man-val">기타</span></div>',
    t
)

# 30. auto-target 드롭다운 "IF만 (13-18)" 제거
t = t.replace(
    '          <option value="IF">IF만 (13-18)</option>\n',
    ''
)

# 9. 스태거드 버튼 + JS 추가
stagger_js = (
    '\nfunction staggerStart() {\n'
    '  var interval = parseInt(prompt("그룹 간격 (초, 기본 20):", "20") || "20");\n'
    '  var grp      = parseInt(prompt("그룹 크기 (기본 5):", "5") || "5");\n'
    '  if (isNaN(interval) || isNaN(grp)) return;\n'
    '  if (!mqttClient || !mqttClient.connected) { alert("MQTT 미연결"); return; }\n'
    '  mqttClient.publish("gb16/all/cmd", "STAGGER_HEAT interval=" + interval + " group=" + grp, { qos: 1 });\n'
    '  logCmd("🎲 스태거드 시작 — " + interval + "s 간격 / " + grp + "대씩");\n'
    '}\n'
    'function staggerStop() {\n'
    '  if (!mqttClient || !mqttClient.connected) { alert("MQTT 미연결"); return; }\n'
    '  mqttClient.publish("gb16/all/cmd", "STAGGER_STOP", { qos: 1 });\n'
    '  logCmd("⛔ 스태거드 중단");\n'
    '}\n'
)
t = t.replace('</script>\n</body>', stagger_js + '</script>\n</body>', 1)

stagger_btns = (
    '        <button class="cbtn" style="background:#7b4fc8;color:#fff" '
    'onclick="staggerStart()">🎲 스태거드 시작</button>\n'
    '        <button class="cbtn" onclick="staggerStop()">⏹ 스태거드 중단</button>'
)
t = t.replace(
    '<button class="cbtn send"   onclick="sendSet()">📋 SET 전송</button>',
    '<button class="cbtn send"   onclick="sendSet()">📋 SET 전송</button>\n' + stagger_btns
)

# 31. IF Ceiling 파라미터 탭 추가
t = t.replace(
    '<div class="ptab active" id="tab-if" onclick="switchParamTab(\'if\')">IF</div>\n      </div>',
    '<div class="ptab active" id="tab-if"   onclick="switchParamTab(\'if\')">IF</div>\n'
    '        <div class="ptab"        id="tab-ceil" onclick="switchParamTab(\'ceil\')">IF Ceiling</div>\n'
    '      </div>'
)

# 32. params-ceil 슬라이더 블록 삽입 (params-if 닫힌 직후)
ceil_params = (
    '\n      <!-- IF Ceiling 파라미터 -->\n'
    '      <div id="params-ceil" style="display:none">\n'
    '        <div class="param-row">\n'
    '          <label>온도 목표</label>\n'
    '          <input type="range" id="ceil-temp" min="20" max="60" value="40"\n'
    '            oninput="document.getElementById(\'ceil-temp-v\').textContent=this.value+\'°C\'">\n'
    '          <span class="param-val" id="ceil-temp-v">40°C</span>\n'
    '        </div>\n'
    '        <div class="param-row">\n'
    '          <label>유지 시간</label>\n'
    '          <input type="range" id="ceil-sust" min="0" max="60" value="5"\n'
    '            oninput="document.getElementById(\'ceil-sust-v\').textContent=this.value+\'s\'">\n'
    '          <span class="param-val" id="ceil-sust-v">5s</span>\n'
    '        </div>\n'
    '        <div class="param-row">\n'
    '          <label>팬 속도</label>\n'
    '          <input type="range" id="ceil-fan" min="0" max="255" value="150"\n'
    '            oninput="document.getElementById(\'ceil-fan-v\').textContent=this.value">\n'
    '          <span class="param-val" id="ceil-fan-v">150</span>\n'
    '        </div>\n'
    '        <div class="param-row">\n'
    '          <label>최저 온도</label>\n'
    '          <input type="range" id="ceil-tmin" min="0" max="30" value="0"\n'
    '            oninput="document.getElementById(\'ceil-tmin-v\').textContent=this.value===0?\'amb\':this.value+\'°C\'">\n'
    '          <span class="param-val" id="ceil-tmin-v">amb</span>\n'
    '        </div>\n'
    '        <div class="check-row">\n'
    '          <input type="checkbox" id="ceil-loop">\n'
    '          <label for="ceil-loop">루프 반복</label>\n'
    '        </div>\n'
    '      </div>'
)
t = t.replace(
    '        <div class="check-row">\n'
    '          <input type="checkbox" id="if-loop">\n'
    '          <label for="if-loop">루프 반복</label>\n'
    '        </div>\n'
    '      </div>\n'
    '    </div>\n'
    '\n'
    '    <!-- 명령 -->',
    '        <div class="check-row">\n'
    '          <input type="checkbox" id="if-loop">\n'
    '          <label for="if-loop">루프 반복</label>\n'
    '        </div>\n'
    '      </div>'
    + ceil_params +
    '\n    </div>\n'
    '\n'
    '    <!-- 명령 -->'
)

# 33. switchParamTab → params-ceil / tab-ceil 처리 추가
t = re.sub(
    r'function switchParamTab\(tab\) \{.*?'
    r'if \(tgt\) tgt\.value = tab === .gp. \? .GP. : .IF.;\n\}',
    'function switchParamTab(tab) {\n'
    '  paramTab = tab;\n'
    "  document.getElementById('params-if').style.display   = tab === 'if'   ? '' : 'none';\n"
    "  document.getElementById('params-ceil').style.display = tab === 'ceil' ? '' : 'none';\n"
    "  document.getElementById('tab-if').classList.toggle('active',   tab === 'if');\n"
    "  document.getElementById('tab-ceil').classList.toggle('active', tab === 'ceil');\n"
    '}',
    t, flags=re.DOTALL
)

# 34. activeFanVal() 추가 + FAN_ON 버튼 교체
t = t.replace(
    "function ifFanVal()  { return document.getElementById('if-fan').value; }",
    "function ifFanVal()    { return document.getElementById('if-fan').value; }\n"
    "function ceilFanVal()  { return document.getElementById('ceil-fan').value; }\n"
    "function activeFanVal(){ return paramTab === 'ceil' ? ceilFanVal() : ifFanVal(); }"
)
t = t.replace(
    "sendSelected('FAN_ON speed='+(paramTab==='gp'?gpFanVal():ifFanVal()))",
    "sendSelected('FAN_ON speed='+activeFanVal())"
)
t = t.replace(
    "sendSelected('FAN_ON speed='+ifFanVal())",
    "sendSelected('FAN_ON speed='+activeFanVal())"
)

# 35. sendSet() → IF(1-30) / IF Ceiling(31-34) 분리
t = re.sub(
    r'function sendSet\(\) \{.*?logCmd\(lines\.join\(.\\n.\)\);\n\}',
    'function sendSet() {\n'
    '  const targets = resolveTargets();\n'
    "  if (targets.length === 0) { logCmd('⚠ 대상 없음'); return; }\n"
    '  const ifTargets   = targets.filter(id => !IF_CEIL.includes(id));  // R01~R30\n'
    '  const ceilTargets = targets.filter(id =>  IF_CEIL.includes(id));  // R31~R34\n'
    '  let lines = [];\n'
    '\n'
    '  if (ifTargets.length) {\n'
    "    const t  = document.getElementById('if-temp').value;\n"
    "    const s  = document.getElementById('if-sust').value;\n"
    "    const f  = document.getElementById('if-fan').value;\n"
    "    const tm = document.getElementById('if-tmin').value;\n"
    "    const lp = document.getElementById('if-loop').checked ? 1 : 0;\n"
    '    const cmd = `SET temp=${t} sust=${s} fan=${f} tmin=${tm} loop=${lp}`;\n'
    '    ifTargets.forEach(rid => publish(rid, cmd));\n'
    "    lines.push(`✅ IF R[${ifTargets.join(',')}] → ${cmd}`);\n"
    '  }\n'
    '  if (ceilTargets.length) {\n'
    "    const t  = document.getElementById('ceil-temp').value;\n"
    "    const s  = document.getElementById('ceil-sust').value;\n"
    "    const f  = document.getElementById('ceil-fan').value;\n"
    "    const tm = document.getElementById('ceil-tmin').value;\n"
    "    const lp = document.getElementById('ceil-loop').checked ? 1 : 0;\n"
    '    const cmd = `SET temp=${t} sust=${s} fan=${f} tmin=${tm} loop=${lp}`;\n'
    '    ceilTargets.forEach(rid => publish(rid, cmd));\n'
    "    lines.push(`✅ IF Ceiling R[${ceilTargets.join(',')}] → ${cmd}`);\n"
    '  }\n'
    "  logCmd(lines.join('\\n'));\n"
    '}',
    t, flags=re.DOTALL
)

f.write_text(t, encoding='utf-8')
print("[OK] gb16_control.html GB16 변환 완료")
PYEOF
echo "[OK] gb16_control.html"

# ── 3. mqtt.min.js 복사 (MQTT 클라이언트 라이브러리) ─────────────
if [ -f "$SRC_DIR/mqtt.min.js" ]; then
    cp "$SRC_DIR/mqtt.min.js" "$SCRIPT_DIR/mqtt.min.js"
    echo "[OK] mqtt.min.js"
fi

echo ""
echo "═══════════════════════════════════════════════════"
echo " 완료"
echo " 접속 URL (설치 후):"
echo "   로컬 : http://$GB16_HOST_IP:$STATS_API_PORT/dashboard.html"
echo "   외부 : https://$NGROK_DOMAIN/dashboard.html"
echo ""
echo " ⚠️  아직 변환이 필요한 항목:"
echo "   .env 에 GB16_HOST_IP 와 NGROK_DOMAIN 을 설정했는지 확인"
echo "   로컬 Mosquitto 브로커 포트: $MQTT_LOCAL_PORT (start.sh 실행 전 설치)"
echo "═══════════════════════════════════════════════════"
