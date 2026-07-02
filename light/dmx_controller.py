#!/usr/bin/env python3
"""
SeMA 2026 North — DMX 조명 컨트롤러  v1.0
light/dmx_controller.py

18개 Gantom RGBW (4ch) 조명 제어
USB: ENTTEC Open DMX / FT232R  →  DMX 512

DMX 주소 배치:
  L01  ch  1- 4  황금빛 꽃 1    (GP 1-8, 외곽)
  L02  ch  5- 8  황금빛 꽃 2    (GP 1-8, 외곽)
  L03  ch  9-12  황금빛 꽃 3    (GP 1-8, 외곽)
  L04  ch 13-16  황금빛 꽃 4    (GP 1-8, 외곽)
  L05  ch 17-20  황금빛 꽃 5    (GP 1-8, 외곽)
  L06  ch 21-24  황금빛 꽃 6    (GP 1-8, 외곽)
  L07  ch 25-28  아해들 1       (추후 별도 제어)
  L08  ch 29-32  아해들 2       (전체 평균)
  L09  ch 33-36  아해들 3       (전체 평균)
  L10  ch 37-40  아해들 4       (전체 평균)
  L11  ch 41-44  아해들 5       (전체 평균)
  L12  ch 45-48  아해들 6       (전체 평균)
  L13  ch 49-52  황금빛 꽃잎2-A (GP 11-12)
  L14  ch 53-56  황금빛 꽃잎2-B (GP 11-12)
  L15  ch 57-60  황금빛 꽃잎2-C (GP 11-12)
  L16  ch 61-64  황금빛 꽃잎2-D (GP 11-12)
  L17  ch 65-68  황금빛 꽃잎1-A (GP 9-10)
  L18  ch 69-72  황금빛 꽃잎1-B (GP 9-10)
  L19  ch 73-76  황금빛 꽃잎1-C (GP 9-10)
  L20  ch 77-80  황금빛 꽃잎1-D (GP 9-10)

MQTT 수신:
  sma/robot/+/log   → 로봇 상태 (state, s1, s2, tmax, elapsed)
  audience/present  → 관객 감지 (true/false)
  sma/light/cmd     → 조명 제어 명령

MQTT 발행:
  sma/light/status  → 현재 조명 상태 (2초 주기, 대시보드용)

sma/light/cmd 명령 형식:
  MODE:AUTO          자동 (로봇 상태 반응)
  MODE:MANUAL        수동 (SET 명령으로 직접 제어)
  BRIGHTNESS:0.8     마스터 밝기 (0.0~1.0)
  SET:1:255,140,0,120  조명 1번 RGBW 수동 지정
  SET:all:0,0,0,0    전체 조명 수동 지정
  CLEAR              수동 오버라이드 해제 → AUTO 복귀
  SCENE:DAY          낮 씬 강제 (time_factor=0.7)
  SCENE:NIGHT        밤 씬 강제 (time_factor=1.0)
  SCENE:AUTO         시간 자동
"""

import os
import sys
import json
import math
import time
import uuid
import logging
import threading
from pathlib import Path
from collections import deque
from typing import Optional

import ctypes
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# ─── 경로 / 환경변수 ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
load_dotenv(SCRIPT_DIR.parent / 'tools' / 'mqtt_monitor' / '.env')

DMX_PORT    = os.getenv('DMX_PORT',    '/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A600dJot-if00-port0')
DMX_FPS     = int(os.getenv('DMX_FPS', '30'))
BROKER      = os.getenv('MQTT_BROKER', 'broker.hivemq.com')
BROKER_PORT = int(os.getenv('MQTT_PORT', '1883'))
MQTT_USER   = os.getenv('MQTT_USER', '')
MQTT_PASS   = os.getenv('MQTT_PASS', '')

# ─── 로깅 ─────────────────────────────────────────────────────────
LOG_DIR = SCRIPT_DIR.parent / 'host' / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [LIGHT] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / 'light.log', encoding='utf-8'),
    ]
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  DMX 주소 배치  (Gantom RGBW 4ch)
#  각 조명: ch(1-based), group, label, robots, phase(rad)
# ═══════════════════════════════════════════════════════════════════
LIGHT_MAP = [
    # 황금빛 꽃 — 1~6 (GP 1~8 외곽에서 비춤, 6방향 위상 분산)
    {'id':  1, 'ch':  1, 'group': 'gp_flower', 'label': '황금빛 꽃 1',    'robots': [1,2,3,4,5,6,7,8], 'phase': 0.00},
    {'id':  2, 'ch':  5, 'group': 'gp_flower', 'label': '황금빛 꽃 2',    'robots': [1,2,3,4,5,6,7,8], 'phase': 1.05},
    {'id':  3, 'ch':  9, 'group': 'gp_flower', 'label': '황금빛 꽃 3',    'robots': [1,2,3,4,5,6,7,8], 'phase': 2.09},
    {'id':  4, 'ch': 13, 'group': 'gp_flower', 'label': '황금빛 꽃 4',    'robots': [1,2,3,4,5,6,7,8], 'phase': 3.14},
    {'id':  5, 'ch': 17, 'group': 'gp_flower', 'label': '황금빛 꽃 5',    'robots': [1,2,3,4,5,6,7,8], 'phase': 4.19},
    {'id':  6, 'ch': 21, 'group': 'gp_flower', 'label': '황금빛 꽃 6',    'robots': [1,2,3,4,5,6,7,8], 'phase': 5.24},
    # 아해들 — 7~12 (추후 별도 제어규칙 적용 예정)
    {'id':  7, 'ch': 25, 'group': 'children',  'label': '아해들 1',       'robots': 'all',    'phase': 0.52},
    {'id':  8, 'ch': 29, 'group': 'children',  'label': '아해들 2',       'robots': 'all',    'phase': 1.57},
    {'id':  9, 'ch': 33, 'group': 'children',  'label': '아해들 3',       'robots': 'all',    'phase': 2.62},
    {'id': 10, 'ch': 37, 'group': 'children',  'label': '아해들 4',       'robots': 'all',    'phase': 3.67},
    {'id': 11, 'ch': 41, 'group': 'children',  'label': '아해들 5',       'robots': 'all',    'phase': 4.71},
    {'id': 12, 'ch': 45, 'group': 'children',  'label': '아해들 6',       'robots': 'all',    'phase': 5.76},
    # 황금빛 꽃잎 2 — 13~16 (GP 11~12)
    {'id': 13, 'ch': 49, 'group': 'gp_petal2', 'label': '황금빛 꽃잎2-A', 'robots': [11, 12], 'phase': 0.00},
    {'id': 14, 'ch': 53, 'group': 'gp_petal2', 'label': '황금빛 꽃잎2-B', 'robots': [11, 12], 'phase': 1.57},
    {'id': 15, 'ch': 57, 'group': 'gp_petal2', 'label': '황금빛 꽃잎2-C', 'robots': [11, 12], 'phase': 3.14},
    {'id': 16, 'ch': 61, 'group': 'gp_petal2', 'label': '황금빛 꽃잎2-D', 'robots': [11, 12], 'phase': 4.71},
    # 황금빛 꽃잎 1 — 17~20 (GP 9~10)
    {'id': 17, 'ch': 65, 'group': 'gp_petal1', 'label': '황금빛 꽃잎1-A', 'robots': [9, 10],  'phase': 0.79},
    {'id': 18, 'ch': 69, 'group': 'gp_petal1', 'label': '황금빛 꽃잎1-B', 'robots': [9, 10],  'phase': 2.36},
    {'id': 19, 'ch': 73, 'group': 'gp_petal1', 'label': '황금빛 꽃잎1-C', 'robots': [9, 10],  'phase': 3.93},
    {'id': 20, 'ch': 77, 'group': 'gp_petal1', 'label': '황금빛 꽃잎1-D', 'robots': [9, 10],  'phase': 5.50},
]


# ═══════════════════════════════════════════════════════════════════
#  색상 팔레트  (R, G, B, W) — 상태 × 그룹
#
#  GP 황금빛:  호박색 → 황금색 → 오렌지
#  IF 비결정적 유영:  청록 → 하늘빛 → 깊은 파랑
#  아해들:  따뜻한 백색 (전체 분위기 종합 반영)
# ═══════════════════════════════════════════════════════════════════
GP_PALETTE = {
    'OFFLINE': (  5,   2,   0,   8),   # 꺼진 듯 극히 어둠
    'IDLE':    ( 45,  18,   0,  35),   # 은은한 호박빛
    'HEAT':    (230,  75,   0,  55),   # 오렌지-앰버
    'SUST':    (255, 148,   0, 130),   # 황금빛 만개
    'COOL':    (110,  48,  10,  42),   # 식어가는 여운
    'ERR':     (255,   0,   0,   0),   # 적색 경고
}

IF_PALETTE = {
    'OFFLINE': (  0,   4,  10,   5),   # 어두운 심해
    'IDLE':    (  0,  35,  70,  18),   # 차가운 청록빛
    'HEAT':    (  8,  90, 155,  35),   # 깊은 바다빛
    'SUST':    ( 18, 170, 230,  75),   # 밝은 하늘빛
    'COOL':    (  4,  44,  88,  18),   # 가라앉는 청색
    'ERR':     (  0,   0, 255,   0),   # 청색 경고
}

CH_PALETTE = {
    'OFFLINE': (  5,   5,   5,  12),   # 희미한 흰빛
    'IDLE':    ( 18,  13,   5,  55),   # 따뜻한 흰빛
    'HEAT':    ( 65,  42,  10, 105),   # 황금빛 흰빛
    'SUST':    ( 95,  72,  22, 185),   # 밝은 황금 흰빛
    'COOL':    ( 28,  22,  16,  72),   # 가라앉는 흰빛
    'ERR':     (210,  55,   0,  50),   # 주황 경고
}

PALETTE_MAP = {
    'gp_flower': GP_PALETTE,
    'gp_petal1': GP_PALETTE,
    'gp_petal2': GP_PALETTE,
    'children':  CH_PALETTE,
}

# 상태 우선순위 (여러 로봇 중 대표 상태 선택)
STATE_RANK = {'ERR': 5, 'SUST': 4, 'HEAT': 3, 'COOL': 2, 'IDLE': 1, 'OFFLINE': 0}


# ═══════════════════════════════════════════════════════════════════
#  공유 상태
# ═══════════════════════════════════════════════════════════════════
robot_states:  dict  = {}          # rid → {state, s1, s2, tmax, elapsed}
audience_now:  bool  = False
visit_history: deque = deque()     # 방문 타임스탬프
VISIT_WINDOW   = 600               # 방문 빈도 측정 윈도우(초)

_master_brightness: float = 1.0   # 0.0~1.0
_light_mode:        str   = 'AUTO'  # 'AUTO' | 'MANUAL'
_scene_override:    str   = 'AUTO'  # 'AUTO' | 'DAY' | 'NIGHT'
_manual_overrides:  dict  = {}     # light_id → (R,G,B,W)

dmx_buf = [0] * 512               # DMX 유니버스 (0-based)


# ═══════════════════════════════════════════════════════════════════
#  시간대 팩터
#   낮  (10-17h): 0.70  — 자연광과 경쟁
#   저녁(17-20h): 0.90  — 점점 드라마틱
#   밤  (20-22h): 1.00  — 최대 연출
#   야간(22-10h): 0.25  — 대기 상태
# ═══════════════════════════════════════════════════════════════════
_TIME_SCENES = {
    'DAY':   0.70,
    'NIGHT': 1.00,
}

def time_factor() -> float:
    if _scene_override != 'AUTO':
        return _TIME_SCENES.get(_scene_override, 1.0)
    lt = time.localtime()
    h = lt.tm_hour + lt.tm_min / 60.0
    if   10.0 <= h < 17.0: return 0.70
    elif 17.0 <= h < 20.0: return 0.90
    elif 20.0 <= h < 22.0: return 1.00
    else:                   return 0.25


# ═══════════════════════════════════════════════════════════════════
#  방문 빈도 레벨  (0 낮음 / 1 보통 / 2 높음)
# ═══════════════════════════════════════════════════════════════════
VISIT_BRIGHTNESS = [1.00, 1.00, 1.00]  # 관객감지 비활성화 → 항상 100%

def visit_level() -> int:
    now = time.time()
    recent = sum(1 for t in visit_history if now - t < VISIT_WINDOW)
    if recent <= 1: return 0
    if recent <= 4: return 1
    return 2


# ═══════════════════════════════════════════════════════════════════
#  로봇 평균 상태
# ═══════════════════════════════════════════════════════════════════
def avg_robot_state(robots) -> dict:
    """지정 로봇들의 대표 상태와 온도 계산"""
    if robots == 'all':
        ids = list(robot_states.keys()) if robot_states else list(range(1, 19))
    else:
        ids = robots

    seen, s1s, s2s, els = [], [], [], []
    tmax = 45
    for rid in ids:
        s = robot_states.get(rid)
        if s:
            seen.append(s['state'])
            s1s.append(s['s1'])
            s2s.append(s['s2'])
            els.append(s['elapsed'])
            tmax = s.get('tmax', 45)

    if not seen:
        return {'state': 'OFFLINE', 's1': 22.0, 's2': 22.0, 'elapsed': 0,
                'tmax': 45, 'ratio': 0.0}

    dominant = max(seen, key=lambda st: STATE_RANK.get(st, 0))
    s1  = sum(s1s) / len(s1s)
    s2  = sum(s2s) / len(s2s)
    el  = sum(els) / len(els)
    amb = 22.0
    ratio = min(max((max(s1, s2) - amb) / max(tmax - amb, 1), 0.0), 1.0)

    return {'state': dominant, 's1': s1, 's2': s2, 'elapsed': el,
            'tmax': tmax, 'ratio': ratio}


# ═══════════════════════════════════════════════════════════════════
#  RGBW 계산
# ═══════════════════════════════════════════════════════════════════
def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))

def _lerp_color(ca: tuple, cb: tuple, t: float) -> tuple:
    return tuple(int(_lerp(ca[i], cb[i], t)) for i in range(4))


def compute_rgbw(light: dict, now: float) -> tuple:
    """
    단일 조명의 RGBW(0-255) 계산
    now: 현재 시각(epoch seconds) — 호흡 진동 기준
    """
    lid = light['id']

    # ── 수동 오버라이드 ──────────────────────────────────
    if _light_mode == 'MANUAL' and lid in _manual_overrides:
        r, g, b, w = _manual_overrides[lid]
        scale = _master_brightness
        return (min(255, int(r * scale)), min(255, int(g * scale)),
                min(255, int(b * scale)), min(255, int(w * scale)))

    # ── 로봇 대표 상태 ────────────────────────────────────
    rs    = avg_robot_state(light['robots'])
    state = rs['state']
    ratio = rs['ratio']      # 0(상온) ~ 1(목표온도)
    el    = rs['elapsed']

    # ── 상태별 색상 결정 ──────────────────────────────────
    pal = PALETTE_MAP[light['group']]

    if state == 'HEAT':
        # IDLE → HEAT: 온도 비율로 선형 보간
        base = _lerp_color(pal['IDLE'], pal['HEAT'], ratio)

    elif state == 'COOL':
        # SUST → COOL → IDLE: 2분에 걸쳐 서서히 페이드
        cool_t = min(el / 120.0, 1.0)
        sust_to_cool = _lerp_color(pal['SUST'], pal['COOL'], cool_t * 0.7)
        base = _lerp_color(sust_to_cool, pal['IDLE'], cool_t * 0.3)

    elif state == 'ERR':
        # 1Hz 깜빡임
        blink = 1.0 if int(now * 2) % 2 == 0 else 0.15
        base = tuple(int(v * blink) for v in pal['ERR'])

    else:
        base = pal.get(state, pal['IDLE'])

    # ── 호흡 진동 (각 조명마다 다른 위상) ────────────────
    # 상태별 주기·깊이 — SUST가 가장 활발, IDLE이 가장 느긋
    breath_period = {'IDLE': 7.0, 'HEAT': 4.5, 'SUST': 3.0,
                     'COOL': 8.0, 'ERR': 0.5, 'OFFLINE': 10.0}.get(state, 5.0)
    breath_depth  = {'IDLE': 0.14, 'HEAT': 0.10, 'SUST': 0.08,
                     'COOL': 0.12, 'ERR': 0.0,  'OFFLINE': 0.05}.get(state, 0.10)

    osc = 1.0 + breath_depth * math.sin(
        2 * math.pi * now / breath_period + light['phase']
    )

    # ── 종합 스케일 ──────────────────────────────────────
    scale = osc * VISIT_BRIGHTNESS[visit_level()] * time_factor() * _master_brightness

    R = min(255, int(base[0] * scale))
    G = min(255, int(base[1] * scale))
    B = min(255, int(base[2] * scale))
    W = min(255, int(base[3] * scale))

    return (R, G, B, W)


# ═══════════════════════════════════════════════════════════════════
#  libftdi1 설정
# ═══════════════════════════════════════════════════════════════════
try:
    _libftdi = ctypes.CDLL('libftdi1.so.2')
except OSError:
    log.error('[DMX] libftdi1.so.2 로드 실패. sudo apt install libftdi1')
    _libftdi = None

if _libftdi:
    _libftdi.ftdi_new.restype = ctypes.c_void_p
    _libftdi.ftdi_get_error_string.restype = ctypes.c_char_p

_FTDI_VID  = 0x0403
_FTDI_PID  = 0x6001
_BREAK_ON  = 1
_BREAK_OFF = 0


def _ftdi_serial_from_port(port: str) -> bytes:
    import re
    m = re.search(r'USB_UART_([^-]+)-if', port)
    return m.group(1).encode() if m else b''


# ═══════════════════════════════════════════════════════════════════
#  DMX 출력
# ═══════════════════════════════════════════════════════════════════
def open_dmx_port() -> Optional[ctypes.c_void_p]:
    """libftdi로 ENTTEC Open DMX USB 열기"""
    if not _libftdi:
        return None
    serial_b = _ftdi_serial_from_port(DMX_PORT)
    if not serial_b:
        log.error(f'[DMX] DMX_PORT에서 시리얼 번호를 추출하지 못했습니다: {DMX_PORT!r}')
        log.error('[DMX] 시리얼 없이 열면 XBee 등 다른 FT232R 장치를 잡을 수 있으므로 중단합니다.')
        return None
    ctx = _libftdi.ftdi_new()
    if not ctx:
        log.error('[DMX] ftdi_new 실패')
        return None
    ret = _libftdi.ftdi_usb_open_desc(
        ctypes.c_void_p(ctx), _FTDI_VID, _FTDI_PID,
        None, serial_b)
    if ret < 0:
        msg = _libftdi.ftdi_get_error_string(ctypes.c_void_p(ctx))
        log.error(f'[DMX] USB 열기 실패 ({ret}): {msg.decode() if msg else ""}')
        _libftdi.ftdi_free(ctypes.c_void_p(ctx))
        return None
    _libftdi.ftdi_set_baudrate(ctypes.c_void_p(ctx), 250000)
    _libftdi.ftdi_set_line_property(ctypes.c_void_p(ctx), 8, 2, 0)  # 8N2
    log.info(f'[DMX] libftdi 포트 열림: {DMX_PORT}')
    return ctypes.c_void_p(ctx)


def send_dmx_frame(ctx: ctypes.c_void_p) -> bool:
    """libftdi로 DMX 프레임 전송 (Break + MAB + data).
    반환값: True=성공, False=실패(USB 연결 끊김 등)
    """
    try:
        c_buf = ctypes.create_string_buffer(b'\x00' + bytes(dmx_buf[:512]), 513)
        r1 = _libftdi.ftdi_set_line_property2(ctx, 8, 2, 0, _BREAK_ON)
        time.sleep(0.001)   # Break ≥ 88 µs
        r2 = _libftdi.ftdi_set_line_property2(ctx, 8, 2, 0, _BREAK_OFF)
        time.sleep(0.0001)  # MAB ≥ 8 µs
        r3 = _libftdi.ftdi_write_data(ctx, c_buf, 513)
        if r1 < 0 or r2 < 0 or r3 < 0:
            log.warning(f'[DMX] 전송 오류 (libftdi): break={r1}/{r2} write={r3}')
            return False
        return True
    except Exception as e:
        log.warning(f'[DMX] 전송 예외: {e}')
        return False


def _close_dmx_port(ctx: ctypes.c_void_p) -> None:
    """libftdi 컨텍스트 안전 해제 — USB 분리 후에도 크래시 없이 종료"""
    if not _libftdi or ctx is None:
        return
    try:
        _libftdi.ftdi_usb_close(ctx)
    except Exception:
        pass
    try:
        _libftdi.ftdi_free(ctx)
    except Exception:
        pass


def dmx_loop():
    """DMX 출력 루프 — 별도 스레드 (DMX_FPS Hz)"""
    ctx: Optional[ctypes.c_void_p] = None
    interval = 1.0 / DMX_FPS
    _fail_count = 0

    while True:
        # 포트 재연결
        if ctx is None:
            ctx = open_dmx_port()
            if ctx is None:
                time.sleep(5)
                continue
            _fail_count = 0

        t_start = time.time()

        for light in LIGHT_MAP:
            r, g, b, w = compute_rgbw(light, t_start)
            idx = light['ch'] - 1   # 0-based
            dmx_buf[idx]     = r
            dmx_buf[idx + 1] = g
            dmx_buf[idx + 2] = b
            dmx_buf[idx + 3] = w

        ok = send_dmx_frame(ctx)
        if not ok:
            _fail_count += 1
            if _fail_count >= 3:
                # 연속 3회 실패 → USB 분리로 판단, 컨텍스트 해제 후 재연결 대기
                log.warning('[DMX] USB 연결 끊김 — 포트 해제, 재연결 대기')
                _close_dmx_port(ctx)
                ctx = None
                _fail_count = 0
                time.sleep(3)
            continue
        else:
            _fail_count = 0

        elapsed = time.time() - t_start
        time.sleep(max(0.0, interval - elapsed))


# ═══════════════════════════════════════════════════════════════════
#  MQTT
# ═══════════════════════════════════════════════════════════════════
def on_connect(client, userdata, flags, rc, props=None):
    if rc == 0:
        log.info(f'[MQTT] 연결됨: {BROKER}')
        client.subscribe('sma/robot/+/log',  qos=0)
        client.subscribe('audience/present', qos=1)
        client.subscribe('sma/light/cmd',    qos=1)
    else:
        log.warning(f'[MQTT] 연결 실패 rc={rc}')


def on_disconnect(client, userdata, rc, props=None, rc2=None):
    log.warning('[MQTT] 연결 끊김')


def on_message(client, userdata, msg):
    global audience_now
    topic = msg.topic
    try:
        payload = msg.payload.decode('utf-8', errors='replace').strip()
    except Exception:
        return

    # ── 로봇 상태 파싱 ────────────────────────────────────
    if topic.startswith('sma/robot/') and topic.endswith('/log'):
        parts = topic.split('/')
        try:
            rid = int(parts[2])
        except (ValueError, IndexError):
            return
        kv = {}
        for token in payload.split(';'):
            if '=' in token:
                k, v = token.split('=', 1)
                kv[k.strip()] = v.strip()
        try:
            robot_states[rid] = {
                'state':   kv.get('state', 'IDLE'),
                's1':      float(kv.get('s1',    22)),
                's2':      float(kv.get('s2',    22)),
                'tmax':    int(kv.get('tmax',    45)),
                'elapsed': int(kv.get('el',       0)),
                'preset':  int(kv.get('preset',   0)),
            }
        except (ValueError, TypeError):
            pass

    # ── 관객 감지 ──────────────────────────────────────────
    elif topic == 'audience/present':
        prev = audience_now
        audience_now = payload.lower() == 'true'
        if audience_now and not prev:
            visit_history.append(time.time())
            # 오래된 기록 제거
            cutoff = time.time() - VISIT_WINDOW
            while visit_history and visit_history[0] < cutoff:
                visit_history.popleft()
            log.info(f'[AUD] 관객 등장 — 최근 방문 {len(visit_history)}회 '
                     f'(레벨 {visit_level()})')

    # ── 조명 수동 제어 ────────────────────────────────────
    elif topic == 'sma/light/cmd':
        _handle_cmd(payload)


def _handle_cmd(payload: str):
    global _light_mode, _master_brightness, _manual_overrides, _scene_override

    parts = payload.split(':', 1)
    cmd   = parts[0].strip().upper()
    val   = parts[1].strip() if len(parts) > 1 else ''

    if cmd == 'MODE':
        _light_mode = val.upper()
        log.info(f'[CMD] 모드: {_light_mode}')

    elif cmd == 'SCENE':
        _scene_override = val.upper()
        log.info(f'[CMD] 씬: {_scene_override}')

    elif cmd == 'BRIGHTNESS':
        try:
            _master_brightness = max(0.0, min(1.0, float(val)))
            log.info(f'[CMD] 마스터 밝기: {_master_brightness:.2f}')
        except ValueError:
            pass

    elif cmd == 'SET':
        # 형식: "1:255,140,0,120"  또는  "all:R,G,B,W"
        sub = val.split(':', 1)
        if len(sub) == 2:
            target, rgbw_str = sub[0].strip(), sub[1].strip()
            try:
                rgbw = tuple(int(x) for x in rgbw_str.split(','))
                if len(rgbw) == 4:
                    if target.lower() == 'all':
                        for light in LIGHT_MAP:
                            _manual_overrides[light['id']] = rgbw
                    else:
                        _manual_overrides[int(target)] = rgbw
                    log.info(f'[CMD] SET {target} → {rgbw}')
            except ValueError:
                pass

    elif cmd == 'CLEAR':
        _manual_overrides.clear()
        log.info('[CMD] 오버라이드 해제')

    elif cmd == 'BLACKOUT':
        for light in LIGHT_MAP:
            _manual_overrides[light['id']] = (0, 0, 0, 0)
        _light_mode = 'MANUAL'
        log.info('[CMD] BLACKOUT')


# ═══════════════════════════════════════════════════════════════════
#  MQTT 상태 발행 (대시보드용)  — 2초 주기
# ═══════════════════════════════════════════════════════════════════
def status_publisher(client):
    while True:
        time.sleep(2.0)
        try:
            channels = {}
            for light in LIGHT_MAP:
                idx = light['ch'] - 1
                channels[str(light['id'])] = {
                    'label': light['label'],
                    'group': light['group'],
                    'rgbw':  dmx_buf[idx: idx + 4],
                }
            status = {
                'mode':          _light_mode,
                'brightness':    round(_master_brightness, 2),
                'time_factor':   round(time_factor(), 2),
                'visit_level':   visit_level(),
                'audience':      audience_now,
                'scene':         _scene_override,
                'channels':      channels,
            }
            client.publish('sma/light/status', json.dumps(status), qos=0, retain=True)
        except Exception as e:
            log.warning(f'[PUB] 오류: {e}')


# ═══════════════════════════════════════════════════════════════════
#  MQTT 재연결
# ═══════════════════════════════════════════════════════════════════
def reconnect_loop(client):
    backoff = 5
    while True:
        time.sleep(backoff)
        if not client.is_connected():
            try:
                client.reconnect()
                backoff = 5
            except Exception as e:
                log.warning(f'[MQTT] 재연결 실패: {e}')
                backoff = min(backoff * 2, 60)


# ═══════════════════════════════════════════════════════════════════
#  메인
# ═══════════════════════════════════════════════════════════════════
def main():
    log.info('SeMA 2026 North — DMX 조명 컨트롤러 시작')
    log.info(f'  DMX 포트 : {DMX_PORT}')
    log.info(f'  FPS      : {DMX_FPS}')
    log.info(f'  조명 수  : {len(LIGHT_MAP)}개')
    log.info(f'  MQTT     : {BROKER}:{BROKER_PORT}')

    cid    = f'sema_light_{uuid.uuid4().hex[:8]}'
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=cid, clean_session=True)
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.connect(BROKER, BROKER_PORT, keepalive=60)
    client.loop_start()

    threading.Thread(target=dmx_loop,                         daemon=True).start()
    threading.Thread(target=status_publisher, args=(client,), daemon=True).start()
    threading.Thread(target=reconnect_loop,   args=(client,), daemon=True).start()

    log.info('[LIGHT] 실행 중. Ctrl+C 종료.')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info('[LIGHT] 종료.')
        client.loop_stop()
        sys.exit(0)


if __name__ == '__main__':
    main()
