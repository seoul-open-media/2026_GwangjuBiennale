#!/usr/bin/env python3
"""
sema_host.py  —  SeMA 2026 North  시리얼 게이트웨이
v1.0  (Pure Data 대체)

역할:
  XBee USB (/dev/ttyUSB0, 115200) ↔ bridge.py (UDP)

  수신: 시리얼 → STATUS 패킷(23B) 조립 → UDP:5005 전달 → bridge.py
  송신: UDP:5006 ← bridge.py ← MQTT → CMD 텍스트 파싱 → CMD 패킷(16B) → 시리얼

CMD 텍스트 형식 (bridge.py → UDP:5006):
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

  robot_id 'all' 또는 '*' → 전체 18대 전송

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

UDP_FWD_IP    = os.getenv('UDP_FWD_IP',       '127.0.0.1')   # bridge.py 수신 주소
UDP_FWD_PORT  = int(os.getenv('UDP_FWD_PORT', '5005'))        # STATUS 전달

UDP_RECV_IP   = os.getenv('UDP_RECV_IP',      '0.0.0.0')
UDP_RECV_PORT = int(os.getenv('UDP_RECV_PORT','5006'))         # CMD 수신

LOG_FILE      = os.getenv('HOST_LOG_FILE',    'logs/sema_host.log')

# ─── 상수 ─────────────────────────────────────────────────────────────────────
STATUS_LEN = 23   # STATUS 패킷 크기
CMD_LEN    = 16   # CMD    패킷 크기
START_B    = 0xFF
END_B      = 0xFE

GP_IDS  = set(range(1,  13))   # Golden_Petal         1~12
IF_IDS  = set(range(13, 19))   # Indeterministic_Float 13~18
ALL_IDS = GP_IDS | IF_IDS

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
        # rtscts/dsrdtr/xonxoff 비활성화: TIOCMGET/TIOCMSET ioctl 최소화
        # → USB 드라이버 초기화 중 모뎀 라인 ioctl 크래시 방지
        ser = serial.Serial(
            SERIAL_PORT, BAUD, timeout=0.1,
            rtscts=False, dsrdtr=False, xonxoff=False,
        )
        # HUPCL 비활성화 + CLOCAL 활성화: close() 시 DTR 강하 및 break 전송 억제
        # → serial_break+0x39/0x80 [usbserial] 커널 크래시 방지
        # TCSAFLUSH: 설정 적용 전 입력 버퍼 정리 → "readiness to read but no data" 방지
        try:
            attrs = termios.tcgetattr(ser.fd)
            attrs[2] &= ~termios.HUPCL    # 닫을 때 DTR 강하 억제
            attrs[2] |=  termios.CLOCAL   # 모뎀 제어 라인 무시 (DCD/CTS hangup 차단)
            termios.tcsetattr(ser.fd, termios.TCSAFLUSH, attrs)  # FLUSH: 잔류 바이트 제거
        except Exception as e:
            log.warning(f'[SERIAL] HUPCL/CLOCAL 설정 실패 (무시): {e}')
        return ser
    except (serial.SerialException, OSError):
        return None


def _close_serial_safe(ser: serial.Serial) -> None:
    """USB 분리 후에도 안전하게 닫기.
    pyserial.close() 내부의 termios/ioctl 호출을 우회하고
    fd를 직접 os.close() 하여 커널 크래시를 방지한다.
    """
    try:
        fd = getattr(ser, 'fd', None)
        # pyserial 내부 상태를 먼저 무효화 → close()가 ioctl 호출 안 하도록
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
class GpParams:
    """Golden_Petal CMD 파라미터 (robot_id 1~12)"""
    mode    : int = 1
    temp1   : int = 45
    sust1   : int = 5
    fan1    : int = 200
    fanOnly1: int = 0
    temp2   : int = 45
    sust2   : int = 5
    fan2    : int = 200
    fanOnly2: int = 0
    loop    : int = 0
    preset  : int = 0
    sound   : int = 0
    volume  : int = 0   # 0=변경없음, 1~100


@dataclass
class IfParams:
    """Indeterministic_Float CMD 파라미터 (robot_id 13~18)"""
    mode    : int = 1
    temp    : int = 40
    sust    : int = 15
    fan     : int = 150
    fanOnly : int = 0
    tmin    : int = 0
    loop    : int = 0
    preset  : int = 0
    sound   : int = 0
    volume  : int = 0   # 0=변경없음, 1~100


_gp: dict = {rid: GpParams() for rid in GP_IDS}
_if: dict = {rid: IfParams() for rid in IF_IDS}

def gp_p(rid: int) -> GpParams:
    return _gp.setdefault(rid, GpParams())

def if_p(rid: int) -> IfParams:
    return _if.setdefault(rid, IfParams())


# ══════════════════════════════════════════════════════════════════════════════
#  패킷 조립
# ══════════════════════════════════════════════════════════════════════════════
def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


def build_gp_cmd(rid: int, p: GpParams) -> bytes:
    """Golden_Petal CMD 패킷 (16 bytes)
    b0=START  b1=rid  b2=mode  b3=temp1  b4=sust1  b5=fan1  b6=fanOnly1
    b7=temp2  b8=sust2  b9=fan2  b10=fanOnly2  b11=volume  b12=loop  b13=preset
    b14=sound  b15=END
    """
    return bytes([
        START_B,
        rid & 0xFF,
        _clamp(p.mode,     1, 2),
        _clamp(p.temp1,    0, 60),
        _clamp(p.sust1,    0, 60),
        _clamp(p.fan1,     0, 255),
        p.fanOnly1 & 1,
        _clamp(p.temp2,    0, 60),
        _clamp(p.sust2,    0, 60),
        _clamp(p.fan2,     0, 255),
        p.fanOnly2 & 1,
        _clamp(p.volume,   0, 100),
        p.loop   & 1,
        _clamp(p.preset,   0, 7),
        _clamp(p.sound,    0, 4),
        END_B,
    ])


def build_if_cmd(rid: int, p: IfParams) -> bytes:
    """Indeterministic_Float CMD 패킷 (16 bytes)
    b0=START  b1=rid  b2=mode  b3=temp  b4=sust  b5=fan  b6=fanOnly
    b7=tmin  b8~b10=0  b11=volume  b12=loop  b13=preset  b14=sound  b15=END
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
        0, 0, 0,
        _clamp(p.volume,  0, 100),
        p.loop   & 1,
        _clamp(p.preset,  0, 7),
        _clamp(p.sound,   0, 4),
        END_B,
    ])


def build_stop(rid: int) -> bytes:
    """RESET / HEAT_OFF — 모든 출력 OFF → IDLE
    mode=1, tempTarget=0, fanSpeed=0 → 펌웨어: fanSpeed==0&&tempTarget==0 → allOff→IDLE
    """
    return bytes([START_B, rid, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, END_B])


def build_fan(rid: int, speed: int) -> bytes:
    """팬 전용 ON (fanOnly=1)"""
    s = _clamp(speed, 0, 255)
    if rid in GP_IDS:
        # Unit1 + Unit2 모두 팬 전용
        return bytes([START_B, rid, 1, 0, 0, s, 1, 0, 0, s, 1, 0, 0, 0, 0, END_B])
    else:
        return bytes([START_B, rid, 1, 0, 0, s, 1, 0, 0, 0, 0, 0, 0, 0, 0, END_B])


def build_sound_only(rid: int, snd: int, loop: int = 0) -> bytes:
    """사운드만 재생, 상태 변경 없음
    mode=2 + presetNum=0 + b11=0 → 펜웨어: sound 실행 후 return
    loop=1이면 반복재생 (b12=1)
    """
    return bytes([START_B, rid, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, loop & 1, 0, _clamp(snd, 0, 4), END_B])


def build_volume_only(rid: int, vol: int) -> bytes:
    """볼륨 전용 패킷 (b11=volume, sound=0)
    mode=2 + presetNum=0 + b11!=0 → 펜웨어: 볼륨만 변경 후 return
    """
    v = _clamp(vol, 1, 100)
    return bytes([START_B, rid, 2, 0, 0, 0, 0, 0, 0, 0, 0, v, 0, 0, 0, END_B])


def build_preset(rid: int, n: int, sound: int = 0) -> bytes:
    """프리셋 실행 (mode=2)"""
    if rid in GP_IDS:
        p = GpParams(mode=2, preset=n, sound=sound)
        return build_gp_cmd(rid, p)
    else:
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
            log.warning(f'[CMD] 범위 외 robot_id: {rid}  (유효: 1~18)')
            continue

        pkt: Optional[bytes] = None

        # ── RESET / HEAT_OFF / FAN_OFF ──────────────────────────────────────
        if cmd_upper in ('RESET', 'HEAT_OFF', 'FAN_OFF'):
            pkt = build_stop(rid)

        # ── HEAT_ON ─────────────────────────────────────────────────────────
        elif cmd_upper == 'HEAT_ON':
            if rid in GP_IDS:
                pkt = build_gp_cmd(rid, gp_p(rid))
            else:
                pkt = build_if_cmd(rid, if_p(rid))

        # ── FAN_ON [speed=N] ────────────────────────────────────────────────
        elif cmd_upper == 'FAN_ON':
            speed = 200
            for t in tokens[1:]:
                if t.lower().startswith('speed='):
                    try:
                        speed = int(t.split('=', 1)[1])
                    except ValueError:
                        pass
            pkt = build_fan(rid, speed)

        # ── PRESET:<n> ──────────────────────────────────────────────────────
        elif cmd_upper.startswith('PRESET:'):
            try:
                n = int(cmd_upper.split(':', 1)[1])
            except (ValueError, IndexError):
                log.warning(f'[CMD] PRESET 번호 오류: {cmd_str!r}')
                continue
            pkt = build_preset(rid, n)

        # ── SOUND:<n> 또는 SOUND:<n>:<loop> ─────────────────────────────────
        elif cmd_upper.startswith('SOUND:'):
            try:
                parts = cmd_upper.split(':')
                snd  = int(parts[1])
                loop = int(parts[2]) if len(parts) > 2 else 0
            except (ValueError, IndexError):
                log.warning(f'[CMD] SOUND 번호 오류: {cmd_str!r}')
                continue
            pkt = build_sound_only(rid, snd, loop)
        # ── VOLUME:<n> ─────────────────────────────────────────────
        elif cmd_upper.startswith('VOLUME:'):
            try:
                vol = int(cmd_upper.split(':', 1)[1])
            except (ValueError, IndexError):
                log.warning(f'[CMD] VOLUME 번호 오류: {cmd_str!r}')
                continue
            pkt = build_volume_only(rid, vol)
        # ── SET key=val ... ─────────────────────────────────────────────────
        elif cmd_upper == 'SET':
            kvs: dict = {}
            for t in tokens[1:]:
                if '=' in t:
                    k, v = t.split('=', 1)
                    try:
                        kvs[k.lower()] = int(v)
                    except ValueError:
                        log.warning(f'[CMD] SET 값 오류: {t!r}')

            if rid in GP_IDS:
                p = gp_p(rid)
                _gp_set(p, kvs)
                pkt = build_gp_cmd(rid, p)
            else:
                p = if_p(rid)
                _if_set(p, kvs)
                pkt = build_if_cmd(rid, p)

        else:
            log.warning(f'[CMD] 알 수 없는 명령: {cmd_str!r}  (R{rid})')

        if pkt is not None:
            send_queue.put(pkt)
            log.info(f'[CMD→TX] R{rid:2d} {cmd_str:<25s} | {pkt.hex(" ")}')


def _gp_set(p: GpParams, kvs: dict) -> None:
    """SET 명령에서 GP 파라미터 갱신"""
    mapping = {
        'mode':     'mode',
        'temp1':    'temp1',
        'sust1':    'sust1',
        'fan1':     'fan1',
        'fanonly1': 'fanOnly1',
        'temp2':    'temp2',
        'sust2':    'sust2',
        'fan2':     'fan2',
        'fanonly2': 'fanOnly2',
        'loop':     'loop',
        'sound':    'sound',
        'volume':   'volume',
    }
    for k, v in kvs.items():
        attr = mapping.get(k)
        if attr:
            setattr(p, attr, v)
        else:
            log.warning(f'[SET] GP 알 수 없는 키: {k!r}')


def _if_set(p: IfParams, kvs: dict) -> None:
    """SET 명령에서 IF 파라미터 갱신"""
    mapping = {
        'mode':    'mode',
        'temp':    'temp',
        'sust':    'sust',
        'fan':     'fan',
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
    """시리얼에서 STATUS 패킷(23B) 조립 → UDP:5005 전달
    프로토콜: START=0xFF, END=0xFE, 데이터 바이트 ≤ 0xFD (253-인코딩)
    → 0xFF는 패킷 시작에만 등장 → buf.find(0xFF) 로 안전하게 동기화
    """
    fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    buf      = bytearray()
    # 스레드 시작 시 잔류 바이트 제거 (크래시 루프 후 버퍼에 쌓인 쓰레기 데이터 방지)
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

            # STATUS 패킷 스캔 (여러 패킷이 버퍼에 있을 수 있음)
            while len(buf) >= STATUS_LEN:
                idx = buf.find(START_B)
                if idx == -1:
                    buf.clear()
                    break
                if idx > 0:
                    buf = buf[idx:]   # 선행 쓰레기 바이트 제거

                if len(buf) < STATUS_LEN:
                    break

                if buf[STATUS_LEN - 1] == END_B:
                    pkt = bytes(buf[:STATUS_LEN])
                    buf = buf[STATUS_LEN:]
                    fwd_sock.sendto(pkt, (UDP_FWD_IP, UDP_FWD_PORT))
                    log.debug(f'[RX] R{pkt[1]-50:2d} STATUS → UDP:{UDP_FWD_PORT}')
                else:
                    # END 바이트 불일치: 이 0xFF는 실제 START 아님 → 건너뜀
                    buf = buf[1:]

        except (serial.SerialException, OSError) as e:
            log.error(f'[RX] 시리얼 연결 끊김: {e}')
            disconnected.set()   # main()에 재연결 요청 (_stop은 건드리지 않음)
            break
        except Exception as e:
            log.error(f'[RX] 예외: {e}', exc_info=True)

    fwd_sock.close()
    log.info('[RX] 수신 스레드 종료')


# ══════════════════════════════════════════════════════════════════════════════
#  스레드: UDP 수신 ← bridge.py → 큐 전달
# ══════════════════════════════════════════════════════════════════════════════
def udp_rx_thread(send_queue: queue.Queue) -> None:
    """UDP:5006 에서 텍스트 CMD 수신 → 바이너리 패킷 조립 → send_queue"""
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
    """send_queue 에서 CMD 패킷을 꺼내 시리얼 송신.
    같은 로봇에 대한 연속 전송은 CMD_RATE_MS 이상 간격 유지.
    """
    last_sent: dict = {}   # rid → last send time (monotonic)
    last_any   = 0.0       # 마지막 전송 시각 (global inter-packet delay용)
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

        # ── 패킷 간 최소 간격 (XBee TX 버퍼 보호) ──────────────────────────
        global_wait = INTER_PKT_MS / 1000.0 - (now - last_any)
        if global_wait > 0:
            time.sleep(global_wait)

        # ── 동일 로봇 최소 간격 ─────────────────────────────────────────────
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
            disconnected.set()   # main()에 재연결 요청
            break

    log.info('[TX] 송신 스레드 종료')


# ══════════════════════════════════════════════════════════════════════════════
#  메인  —  USB 재연결 루프 포함
# ══════════════════════════════════════════════════════════════════════════════
def main() -> None:
    log.info('═' * 55)
    log.info(' sema_host.py  SeMA 2026 North  v1.1')
    log.info(f' 시리얼 : {SERIAL_PORT}  {BAUD} bps')
    log.info(f' STATUS → UDP {UDP_FWD_IP}:{UDP_FWD_PORT}')
    log.info(f' CMD    ← UDP {UDP_RECV_IP}:{UDP_RECV_PORT}')
    log.info('═' * 55)

    # send_queue 는 재연결 사이에도 유지 (재연결 후 쌓인 CMD 전송)
    send_queue: queue.Queue = queue.Queue(maxsize=200)

    # UDP 수신 스레드는 시리얼과 독립적으로 항상 실행
    udp_thread = threading.Thread(
        target=udp_rx_thread, args=(send_queue,), daemon=True, name='udp-rx'
    )
    udp_thread.start()

    reconnect_attempt = 0
    _dev_was_present = False   # USB race condition 방지: 직전 루프에서 장치 존재 여부
    while not _stop.is_set():
        # ── 시리얼 포트 대기 ──────────────────────────────────────────────────
        ser: Optional[serial.Serial] = None
        while not _stop.is_set():
            dev_now_present = os.path.exists(SERIAL_PORT)

            if dev_now_present and not _dev_was_present:
                # 장치가 방금 나타남 → USB 드라이버 완전 초기화까지 대기
                # serial.Serial() 을 너무 빨리 호출하면 드라이버 미초기화 상태에서
                # ioctl 이 커널 크래시를 유발 (serial_break race condition)
                log.info('[MAIN] USB 장치 감지 — 드라이버 안정화 대기 (3s)...')
                time.sleep(3.0)

            _dev_was_present = dev_now_present

            ser = _open_serial_safe()
            if ser is not None:
                # USB 재연결 직후 커널 드라이버(cdc_acm/cp210x) 안정화 대기
                # → open() 직후 read()를 너무 빨리 호출하면 드라이버 미초기화 크래시
                time.sleep(0.5)
                break
            reconnect_attempt += 1
            log.warning(f'[MAIN] 시리얼 대기 중... (시도 {reconnect_attempt})')
            time.sleep(2)

        if _stop.is_set():
            break

        reconnect_attempt = 0
        log.info(f'[MAIN] 시리얼 연결됨: {SERIAL_PORT}')

        # 이번 세션의 연결 끊김 신호 (RX/TX 스레드가 세트)
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

        # ── 연결 끊김 또는 종료 신호까지 대기 ────────────────────────────────
        while not _stop.is_set() and not disconnected.is_set():
            time.sleep(0.5)

        # ── 스레드 먼저 종료 대기 → 그 뒤 fd 닫기 (커널 크래시 방지) ──────────
        # 반드시 join() 이후에 os.close(fd) 해야 한다.
        # 스레드가 ser.write()/ser.read() 도중에 fd가 닫히면
        # USB 재연결 시 cdc_acm/cp210x 드라이버 내부 use-after-free → 커널 크래시.
        disconnected.set()   # _stop 케이스에도 스레드 조기 종료 유도
        for t in serial_threads:
            t.join(timeout=3.0)

        _close_serial_safe(ser)

        if not _stop.is_set():
            log.info('[MAIN] USB 분리 감지 — 재연결 대기 (2s)...')
            time.sleep(2)

    # ── 최종 종료 ─────────────────────────────────────────────────────────────
    log.info('[MAIN] 종료 중...')
    _stop.set()
    udp_thread.join(timeout=3.0)
    log.info('[MAIN] 종료 완료')


if __name__ == '__main__':
    main()
