#!/usr/bin/env python3
"""
gb16_host.py  —  GB16 2026 광주비엔날레  시리얼 게이트웨이
v1.0  (sema_host.py 기반)

역할:
  XBee USB (/dev/xbee, 115200) ↔ gb16_bridge.py (UDP)

  수신: 시리얼 → STATUS 패킷(23B) 조립 → UDP:6005 전달 → gb16_bridge.py
  송신: UDP:6006 ← gb16_bridge.py ← MQTT → CMD 텍스트 파싱 → CMD 패킷(16B) → 시리얼

CMD 텍스트 형식 (gb16_bridge.py → UDP:6006):
  "<robot_id>;<command>\\n"

  지원 명령:
    RESET                       전체 정지 (→ IDLE)
    HEAT_ON                     저장된 파라미터로 가열 시작
    HEAT_OFF                    가열 중지 (→ IDLE)
    FAN_ON [speed=N]            팬 전용 ON  (기본 200)
    FAN_OFF                     팬 OFF (→ IDLE)
    PRESET:<n>                  프리셋 N 실행 (GP 1~7, IF 1~4)
    SOUND:<n>                   사운드만 재생, 현재 상태 유지 (n=1~4)
    SET key=val [key=val ...]   파라미터 갱신 후 즉시 전송
      GP keys: mode temp1 sust1 fan1 fanonly1 temp2 sust2 fan2 fanonly2 loop sound
      IF keys: mode temp  sust  fan  fanonly  tmin               loop sound

  robot_id 'all' 또는 '*' → 전체 전송

STATUS 패킷: 23B  b0=0xFF  b1=robot_id+50  b22=0xFE
CMD    패킷: 16B  b0=0xFF  b1=robot_id     b15=0xFE
"""

import os
import sys
import copy
import time
import socket
import logging
import threading
import queue
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import termios
import serial          # pyserial
from dotenv import load_dotenv

# ─── .env ────────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / '.env')

SERIAL_PORT   = os.getenv('SERIAL_PORT',      '/dev/xbee')
BAUD          = int(os.getenv('BAUD',         '115200'))

UDP_FWD_IP    = os.getenv('UDP_FWD_IP',       '127.0.0.1')   # gb16_bridge.py 수신 주소
UDP_FWD_PORT  = int(os.getenv('UDP_FWD_PORT', '6005'))        # STATUS 전달

UDP_RECV_IP   = os.getenv('UDP_RECV_IP',      '0.0.0.0')
UDP_RECV_PORT = int(os.getenv('UDP_RECV_PORT','6006'))         # CMD 수신

LOG_FILE      = os.getenv('HOST_LOG_FILE',    'logs/gb16_host.log')

# ─── 상수 ─────────────────────────────────────────────────────────────────────
STATUS_LEN = 23   # STATUS 패킷 크기
CMD_LEN    = 16   # CMD    패킷 크기
START_B    = 0xFF
END_B      = 0xFE

IF_IDS  = set(range(1,  31))        # Indeterministic_Float  1~30
IF_CEILING_IDS = set(range(31, 35)) # IF Ceiling             31~34
ALL_IDS = IF_IDS | IF_CEILING_IDS

CMD_RATE_MS  = 200   # 동일 로봇 연속 전송 최소 간격(ms)
INTER_PKT_MS = 15    # 모든 패킷 사이 최소 간격(ms) — XBee TX 버퍼 보호

# ─── 로깅 ──────────────────────────────────────────────────────────────────
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
    ],
)
log = logging.getLogger(__name__)

# ─── 종료 이벤트 ──────────────────────────────────────────────────────────────
_stop = threading.Event()

def _sig_handler(sig, _frame):
    log.info('[MAIN] 종료 신호 수신')
    _stop.set()

signal.signal(signal.SIGINT,  _sig_handler)
signal.signal(signal.SIGTERM, _sig_handler)


# ══════════════════════════════════════════════════════════════════════════════
#  안전한 시리얼 오픈 / 클로즈
#  ─ USB 뽑힌 상태에서 close() → TIOCCBRK ioctl → kernel serial_break() 크래시
#    방지책 1: HUPCL 비활성화 (닫을 때 DTR drop 및 break 전송 억제)
#    방지책 2: fd를 직접 os.close() 하여 pyserial 내부 ioctl 우회
# ══════════════════════════════════════════════════════════════════════════════
def _open_serial_safe() -> Optional[serial.Serial]:
    """시리얼 포트 오픈 + HUPCL 비활성화 + CLOCAL 활성화.
    반환값: Serial 객체 or None (오픈 실패)
    """
    try:
        ser = serial.Serial(
            SERIAL_PORT, BAUD, timeout=0.1,
            rtscts=False, dsrdtr=False, xonxoff=False,
        )
        try:
            attrs = termios.tcgetattr(ser.fd)
            attrs[2] &= ~termios.HUPCL    # 닫을 때 DTR 강하 억제
            attrs[2] |=  termios.CLOCAL   # 모뎀 제어 라인 무시
            termios.tcsetattr(ser.fd, termios.TCSAFLUSH, attrs)
        except Exception as e:
            log.warning(f'[SERIAL] HUPCL/CLOCAL 설정 실패 (무시): {e}')
        return ser
    except (serial.SerialException, OSError):
        return None


def _close_serial_safe(ser: serial.Serial) -> None:
    """USB 분리 후에도 안전하게 닫기."""
    try:
        fd = getattr(ser, 'fd', None)
        ser.fd       = None
        ser.is_open  = False
        if fd is not None:
            os.close(fd)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  로봇 파라미터
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class IfParams:
    """Indeterministic_Float / IF_Ceiling CMD 파라미터 (robot_id 1~34)"""
    mode    : int = 1
    temp    : int = 40
    sust    : int = 15
    fan     : int = 150
    fanOnly : int = 0
    tmin    : int = 0
    pwm     : int = 0
    loop    : int = 0
    preset  : int = 0
    sound   : int = 0
    volume  : int = 0   # 0=변경없음, 1~100


_if: dict = {rid: IfParams() for rid in ALL_IDS}

def if_p(rid: int) -> IfParams:
    return _if.setdefault(rid, IfParams())


# ══════════════════════════════════════════════════════════════════════════════
#  패킷 조립
# ══════════════════════════════════════════════════════════════════════════════
def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))

def build_if_cmd(rid: int, p: IfParams) -> bytes:
    """Indeterministic_Float CMD 패킷 (16 bytes)
    b0=START  b1=rid  b2=mode  b3=temp  b4=sust  b5=fan  b6=fanOnly
    b7=tmin  b8=pwm  b9~b10=0  b11=volume  b12=loop  b13=preset  b14=sound  b15=END
    """
    return bytes([
        START_B,
        rid & 0xFF,
        _clamp(p.mode,    1, 2),
        _clamp(p.temp,    0, 60),
        _clamp(p.sust,    0, 60),
        _clamp(p.fan,     0, 255),
        p.fanOnly & 1,
        _clamp(p.tmin,    0, 30),
        _clamp(p.pwm,     0, 255),
        0, 0,
        _clamp(p.volume,  0, 100),
        p.loop   & 1,
        _clamp(p.preset,  0, 7),
        _clamp(p.sound,   0, 4),
        END_B,
    ])


def build_stop(rid: int) -> bytes:
    """RESET / HEAT_OFF — 모든 출력 OFF → IDLE"""
    return bytes([START_B, rid, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, END_B])


def build_fan(rid: int, speed: int) -> bytes:
    """팬 전용 ON (fanOnly=1)"""
    s = _clamp(speed, 0, 255)
    return bytes([START_B, rid, 1, 0, 0, s, 1, 0, 0, 0, 0, 0, 0, 0, 0, END_B])


def build_sound_only(rid: int, snd: int, loop: int = 0) -> bytes:
    """사운드만 재생, 상태 변경 없음"""
    return bytes([START_B, rid, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, loop & 1, 0, _clamp(snd, 0, 4), END_B])


def build_volume_only(rid: int, vol: int) -> bytes:
    """볼륨 전용 패킷"""
    v = _clamp(vol, 1, 100)
    return bytes([START_B, rid, 2, 0, 0, 0, 0, 0, 0, 0, 0, v, 0, 0, 0, END_B])


def build_preset(rid: int, n: int, sound: int = 0) -> bytes:
    """프리셋 실행 (mode=2)"""
    p = IfParams(mode=2, preset=n, sound=sound)
    return build_if_cmd(rid, p)


# ══════════════════════════════════════════════════════════════════════════════
#  CMD 텍스트 파싱 → 패킷 큐잉
# ══════════════════════════════════════════════════════════════════════════════
def parse_and_enqueue(raw: str, send_queue: queue.Queue) -> None:
    """
    raw 형식: "<robot_id>;<command>"  (공백·개행 허용)
    예시: "5;HEAT_ON"  "all;PRESET:3"  "13;SET temp=38 sust=20"
    """
    raw = raw.strip()
    if not raw or ';' not in raw:
        log.warning(f'[CMD] 형식 오류 (세미콜론 없음): {raw!r}')
        return

    id_part, cmd_str = raw.split(';', 1)
    id_part = id_part.strip().upper()
    cmd_str = cmd_str.strip()
    tokens  = cmd_str.split()
    if not tokens:
        return
    cmd_upper = tokens[0].upper()

    # ── 대상 로봇 결정 ──────────────────────────────────────────────────────
    if id_part in ('ALL', '*'):
        targets = sorted(ALL_IDS)
    else:
        try:
            rid = int(id_part)
            targets = [rid]
        except ValueError:
            log.warning(f'[CMD] 잘못된 robot_id: {id_part!r}')
            return

    for rid in targets:
        if rid not in ALL_IDS:
            log.warning(f'[CMD] 범위 외 robot_id: {rid}  (유효: 1~34)')
            continue

        pkt: Optional[bytes] = None

        if cmd_upper in ('RESET', 'HEAT_OFF', 'FAN_OFF'):
            pkt = build_stop(rid)

        elif cmd_upper == 'HEAT_ON':
            pkt = build_if_cmd(rid, if_p(rid))

        elif cmd_upper == 'FAN_ON':
            speed = 200
            for t in tokens[1:]:
                if t.lower().startswith('speed='):
                    try:
                        speed = int(t.split('=', 1)[1])
                    except ValueError:
                        pass
            pkt = build_fan(rid, speed)

        elif cmd_upper.startswith('PRESET:'):
            try:
                n = int(cmd_upper.split(':', 1)[1])
            except (ValueError, IndexError):
                log.warning(f'[CMD] PRESET 번호 오류: {cmd_str!r}')
                continue
            pkt = build_preset(rid, n)

        elif cmd_upper.startswith('SOUND:'):
            try:
                parts = cmd_upper.split(':')
                snd  = int(parts[1])
                loop = int(parts[2]) if len(parts) > 2 else 0
            except (ValueError, IndexError):
                log.warning(f'[CMD] SOUND 번호 오류: {cmd_str!r}')
                continue
            pkt = build_sound_only(rid, snd, loop)

        elif cmd_upper.startswith('VOLUME:'):
            try:
                vol = int(cmd_upper.split(':', 1)[1])
            except (ValueError, IndexError):
                log.warning(f'[CMD] VOLUME 번호 오류: {cmd_str!r}')
                continue
            pkt = build_volume_only(rid, vol)

        elif cmd_upper == 'SET':
            kvs: dict = {}
            for t in tokens[1:]:
                if '=' in t:
                    k, v = t.split('=', 1)
                    try:
                        kvs[k.lower()] = int(v)
                    except ValueError:
                        log.warning(f'[CMD] SET 값 오류: {t!r}')

            p = if_p(rid)
            _if_set(p, kvs)
            pkt = build_if_cmd(rid, p)

        else:
            log.warning(f'[CMD] 알 수 없는 명령: {cmd_str!r}  (R{rid})')

        if pkt is not None:
            send_queue.put(pkt)
            log.info(f'[CMD→TX] R{rid:2d} {cmd_str:<25s} | {pkt.hex(" ")}')


def _if_set(p: IfParams, kvs: dict) -> None:
    mapping = {
        'mode':    'mode',
        'temp':    'temp',
        'sust':    'sust',
        'fan':     'fan',
        'pwm':     'pwm',
        'fanonly': 'fanOnly',
        'tmin':    'tmin',
        'loop':    'loop',
        'sound':   'sound',
        'volume':  'volume',
    }
    for k, v in kvs.items():
        attr = mapping.get(k)
        if attr:
            setattr(p, attr, v)
        else:
            log.warning(f'[SET] IF 알 수 없는 키: {k!r}')


# ══════════════════════════════════════════════════════════════════════════════
#  스레드: 시리얼 수신 → UDP 전달
# ══════════════════════════════════════════════════════════════════════════════
def serial_rx_thread(ser: serial.Serial, disconnected: threading.Event) -> None:
    """시리얼에서 STATUS 패킷(23B) 조립 → UDP:6005 전달"""
    fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    buf      = bytearray()
    try:
        ser.reset_input_buffer()
    except Exception:
        pass
    log.info(f'[RX] 시리얼 수신 시작  {SERIAL_PORT} @ {BAUD}bps')

    while not _stop.is_set() and not disconnected.is_set():
        try:
            waiting = ser.in_waiting or 1
            chunk   = ser.read(waiting)
            if not chunk:
                continue
            buf.extend(chunk)

            while len(buf) >= STATUS_LEN:
                idx = buf.find(START_B)
                if idx == -1:
                    buf.clear()
                    break
                if idx > 0:
                    buf = buf[idx:]

                if len(buf) < STATUS_LEN:
                    break

                if buf[STATUS_LEN - 1] == END_B:
                    pkt = bytes(buf[:STATUS_LEN])
                    buf = buf[STATUS_LEN:]
                    fwd_sock.sendto(pkt, (UDP_FWD_IP, UDP_FWD_PORT))
                    log.debug(f'[RX] R{pkt[1]-50:2d} STATUS → UDP:{UDP_FWD_PORT}')
                else:
                    buf = buf[1:]

        except (serial.SerialException, OSError) as e:
            log.error(f'[RX] 시리얼 연결 끊김: {e}')
            disconnected.set()
            break
        except Exception as e:
            log.error(f'[RX] 예외: {e}', exc_info=True)

    fwd_sock.close()
    log.info('[RX] 수신 스레드 종료')


# ══════════════════════════════════════════════════════════════════════════════
#  스레드: UDP 수신 ← gb16_bridge.py → 큐 전달
# ══════════════════════════════════════════════════════════════════════════════
def udp_rx_thread(send_queue: queue.Queue) -> None:
    """UDP:6006 에서 텍스트 CMD 수신 → 바이너리 패킷 조립 → send_queue"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(1.0)
    sock.bind((UDP_RECV_IP, UDP_RECV_PORT))
    log.info(f'[UDP] CMD 수신 대기  {UDP_RECV_IP}:{UDP_RECV_PORT}')

    while not _stop.is_set():
        try:
            data, addr = sock.recvfrom(1024)
            text = data.decode('utf-8', errors='replace').strip()
            if text:
                log.debug(f'[UDP] {addr}  {text!r}')
                parse_and_enqueue(text, send_queue)
        except socket.timeout:
            continue
        except Exception as e:
            log.error(f'[UDP] 예외: {e}', exc_info=True)

    sock.close()
    log.info('[UDP] 수신 스레드 종료')


# ══════════════════════════════════════════════════════════════════════════════
#  스레드: 큐 → 시리얼 송신
# ══════════════════════════════════════════════════════════════════════════════
def serial_tx_thread(ser: serial.Serial, send_queue: queue.Queue,
                     disconnected: threading.Event) -> None:
    """send_queue 에서 CMD 패킷을 꺼내 시리얼 송신."""
    last_sent: dict = {}
    last_any   = 0.0
    log.info('[TX] 시리얼 송신 스레드 시작')

    while not _stop.is_set() and not disconnected.is_set():
        try:
            pkt = send_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        if len(pkt) != CMD_LEN or pkt[0] != START_B or pkt[-1] != END_B:
            log.warning(f'[TX] 잘못된 패킷 무시: {pkt.hex(" ")}')
            continue

        rid  = pkt[1]
        now  = time.monotonic()

        global_wait = INTER_PKT_MS / 1000.0 - (now - last_any)
        if global_wait > 0:
            time.sleep(global_wait)

        now  = time.monotonic()
        gap  = now - last_sent.get(rid, 0.0)
        wait = CMD_RATE_MS / 1000.0 - gap
        if wait > 0:
            time.sleep(wait)

        try:
            ser.write(pkt)
            ser.flush()
            last_sent[rid] = time.monotonic()
            last_any       = time.monotonic()
            log.debug(f'[TX] R{rid:2d} ← {pkt.hex(" ")}')
        except (serial.SerialException, OSError) as e:
            log.error(f'[TX] 시리얼 연결 끊김: {e}')
            disconnected.set()
            break

    log.info('[TX] 송신 스레드 종료')


# ══════════════════════════════════════════════════════════════════════════════
#  메인  —  USB 재연결 루프 포함
# ══════════════════════════════════════════════════════════════════════════════
def main() -> None:
    log.info('═' * 55)
    log.info(' gb16_host.py  GB16 2026 광주비엔날레  v1.0')
    log.info(f' 시리얼 : {SERIAL_PORT}  {BAUD} bps')
    log.info(f' STATUS → UDP {UDP_FWD_IP}:{UDP_FWD_PORT}')
    log.info(f' CMD    ← UDP {UDP_RECV_IP}:{UDP_RECV_PORT}')
    log.info('═' * 55)

    send_queue: queue.Queue = queue.Queue(maxsize=200)

    udp_thread = threading.Thread(
        target=udp_rx_thread, args=(send_queue,), daemon=True, name='udp-rx'
    )
    udp_thread.start()

    reconnect_attempt = 0
    _dev_was_present = False
    while not _stop.is_set():
        ser: Optional[serial.Serial] = None
        while not _stop.is_set():
            dev_now_present = os.path.exists(SERIAL_PORT)

            if dev_now_present and not _dev_was_present:
                log.info('[MAIN] USB 장치 감지 — 드라이버 안정화 대기 (3s)...')
                time.sleep(3.0)

            _dev_was_present = dev_now_present

            ser = _open_serial_safe()
            if ser is not None:
                time.sleep(0.5)
                break
            reconnect_attempt += 1
            log.warning(f'[MAIN] 시리얼 대기 중... (시도 {reconnect_attempt})')
            time.sleep(2)

        if _stop.is_set():
            break

        reconnect_attempt = 0
        log.info(f'[MAIN] 시리얼 연결됨: {SERIAL_PORT}')

        disconnected = threading.Event()

        serial_threads = [
            threading.Thread(
                target=serial_rx_thread,
                args=(ser, disconnected),
                daemon=True, name='serial-rx'
            ),
            threading.Thread(
                target=serial_tx_thread,
                args=(ser, send_queue, disconnected),
                daemon=True, name='serial-tx'
            ),
        ]
        for t in serial_threads:
            t.start()

        while not _stop.is_set() and not disconnected.is_set():
            time.sleep(0.5)

        disconnected.set()
        for t in serial_threads:
            t.join(timeout=3.0)

        _close_serial_safe(ser)

        if not _stop.is_set():
            log.info('[MAIN] USB 분리 감지 — 재연결 대기 (2s)...')
            time.sleep(2)

    log.info('[MAIN] 종료 중...')
    _stop.set()
    udp_thread.join(timeout=3.0)
    log.info('[MAIN] 종료 완료')


if __name__ == '__main__':
    main()
