#!/usr/bin/env python3
"""
SMA Controller - PD UDP(raw XBee) ↔ MQTT Bridge  [v2]

흐름 (수신): [로봇 XBee] → [PD comport] --UDP raw--> [이 스크립트] --MQTT--> [원격 모니터]
흐름 (송신): [dashboard/Telegram] → [MQTT cmd] → [이 스크립트] --UDP--> [PD]

설치: pip install paho-mqtt python-dotenv
실행: python bridge.py

XBee STATUS 패킷 구조 (xbee.cpp sendStatus):
  b0  = 255 (START)
  b1  = robot_id + 50
  b2  = fault (0/1)
  b3  = faultCode
  b4  = temp1 MSB,  b5  = temp1 LSB   → temp1 = (b4*253+b5) / 100.0
  b6  = temp2 MSB,  b7  = temp2 LSB
  b8  = state       (0=IDLE 1=HEAT 2=SUST 3=COOL 4=ERR)
  b9  = elapsed MSB, b10 = elapsed LSB
  b11 = switch (0/1)
  b12 = swElapsed MSB, b13 = swElapsed LSB   → sw_el = (b12*253+b13)
  b14 = amb MSB, b15 = amb LSB
  b16 = pkt_num MSB, b17 = pkt_num LSB
  b18 = tempTarget (목표 가열 온도, °C)
  b19 = targetTempMin (쿨링 저점 온도, °C, 0=미설정)
  b20 = mode (1=user_defined 2=play_preset)
  b21 = presetNum
  b22 = 254 (END)
"""

import socket
import uuid
import paho.mqtt.client as mqtt
import threading
import time
import sys
import logging
import sqlite3
import json
import urllib.request
import urllib.parse
from collections import deque
from pathlib import Path

from dotenv import load_dotenv
import os

# ─── .env 로드 ────────────────────────────────────────
load_dotenv(Path(__file__).parent / '.env')

# ─── Tapo (pip install tapo 설치 시 활성화) ─────────────
try:
    from tapo_reset import reset_group_sync, set_group_power_sync
    TAPO_AVAILABLE = True
except ImportError:
    def reset_group_sync(group_id: int, off_sec: int = 5) -> bool: return False  # noqa: E731
    def set_group_power_sync(group_id: int, on: bool) -> bool: return False      # noqa: E731
    TAPO_AVAILABLE = False

UDP_LISTEN_IP    = os.getenv('UDP_LISTEN_IP',    '0.0.0.0')
UDP_PORT         = int(os.getenv('UDP_PORT',     '5005'))
UDP_FORWARD_IP   = os.getenv('UDP_FORWARD_IP',   '127.0.0.1')
UDP_FORWARD_PORT = int(os.getenv('UDP_FORWARD_PORT', '5006'))

BROKER           = os.getenv('MQTT_BROKER',      'broker.hivemq.com')
BROKER_PORT      = int(os.getenv('MQTT_PORT',    '1883'))
MQTT_USER        = os.getenv('MQTT_USER',        '')
MQTT_PASS        = os.getenv('MQTT_PASS',        '')
MQTT_TLS         = os.getenv('MQTT_TLS',         '')   # 비어있지 않으면 TLS 활성화

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_TOKEN',   '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

LOG_FILE         = os.getenv('LOG_FILE',         'bridge.log')
DB_FILE          = os.getenv('DB_FILE',          'sma_data.db')
TAPO_PLUG_MAP: dict = {}
for _k, _v in os.environ.items():
    if _k.startswith('TAPO_GROUP_'):
        try: TAPO_PLUG_MAP[int(_k[len('TAPO_GROUP_'):])] = _v.strip()
        except ValueError: pass
TAPO_GROUP_NAMES: dict = {
    1: '황금칩 꽃',
    2: '황금빛 꽃잎 1',
    3: '황금빛 꽃잎 2',
    4: '비결정적 유영',
    5: '아해들',
}
# ──────────────────────────────────────────────────────

# ─── Logging ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
    ]
)
log = logging.getLogger(__name__)

# ─── 상수 ─────────────────────────────────────────────
PKT_LEN          = 23
START_B          = 255
END_B            = 254
STATE_NAMES      = {0: 'IDLE', 1: 'HEAT', 2: 'SUST', 3: 'COOL', 4: 'ERR!'}

TEMP_RATE_WARN   = 2.0    # ℃/s 이상 상승 시 경고
TEMP_WINDOW      = 10     # 변화율 계산 윈도우(초)
LOSS_RATE_WARN   = 0.10   # 패킷 손실률 10% 이상 경고
OFFLINE_TIMEOUT  = 15     # 초
CMD_ACK_TIMEOUT  = 10     # 초: CMD 발송 후 이 시간 내 응답 없으면 TIMEOUT
DB_RETENTION_DAYS = int(os.getenv('DB_RETENTION_DAYS', '7'))  # DB 보존 일수

# ─── 상태 저장 ────────────────────────────────────────
alerted_fault     = {}
alerted_offline   = {}
alerted_loss      = {}
alerted_temp_rate = {}
last_seen         = {}
first_seen        = {}   # robot_id → 최초 패킷 수신 시각 (리부트 후에도 유지)
last_pkt_num      = {}   # robot_id → last pkt_num
loss_counter      = {}   # robot_id → {'expected': n, 'lost': n}
temp_history      = {}   # robot_id → deque of (ts, s1, s2)
prev_state        = {}   # robot_id → last state string
pending_cmd       = {}   # robot_id(int) → {'payload': str, 'sent_ts': float}

# UDP 역방향 소켓 (MQTT → PD)
fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
# ──────────────────────────────────────────────────────


# ═══════════════════════════════════════════════════════
#  SQLite
# ═══════════════════════════════════════════════════════
def db_init():
    con = sqlite3.connect(DB_FILE)
    con.execute("""
        CREATE TABLE IF NOT EXISTS packets (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        REAL,
            robot_id  INTEGER,
            pkt_num   INTEGER,
            state     TEXT,
            fault     INTEGER,
            code      INTEGER,
            s1        REAL,
            s2        REAL,
            amb       REAL,
            sw        INTEGER,
            elapsed   INTEGER,
            sw_el     INTEGER,
            tmax      INTEGER,
            tmin      INTEGER,
            mode      INTEGER,
            preset    INTEGER,
            loss_pct  REAL,
            rate1     REAL,
            rate2     REAL
        )
    """)
    # ── 통계 테이블 ────────────────────────────────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS audience_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_start    REAL NOT NULL,
            ts_end      REAL,
            duration_s  REAL,
            camera      INTEGER NOT NULL DEFAULT 0  -- 0=전체, 1=CAM1, 2=CAM2
        )
    """)
    # 기존 DB에 camera 컬럼이 없으면 추가 (마이그레이션)
    try:
        con.execute("ALTER TABLE audience_events ADD COLUMN camera INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass  # 이미 있으면 무시
    con.execute("""
        CREATE TABLE IF NOT EXISTS heat_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_start    REAL NOT NULL,
            ts_end      REAL,
            robot_id    INTEGER NOT NULL,
            channel     INTEGER NOT NULL,  -- 1=s1/Unit1, 2=s2/Unit2
            state       TEXT NOT NULL,     -- HEAT or SUST
            duration_s  REAL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS fault_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL NOT NULL,
            robot_id    INTEGER NOT NULL,
            fault_code  INTEGER NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS sound_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL NOT NULL,
            track       INTEGER NOT NULL,
            action      TEXT NOT NULL      -- PLAY, STOP, LOOP
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_audience_start ON audience_events(ts_start)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_heat_robot     ON heat_log(robot_id, ts_start)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_fault_ts       ON fault_log(ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sound_ts       ON sound_log(ts)")
    con.commit()
    # 시작 시 오래된 데이터 즉시 정리
    cutoff = time.time() - DB_RETENTION_DAYS * 86400
    cur = con.execute("DELETE FROM packets WHERE ts < ?", (cutoff,))
    if cur.rowcount:
        deleted = cur.rowcount
        con.commit()
        con.execute("VACUUM")
        log.info(f"[DB] 시작 정리: {deleted}행 삭제 (>{DB_RETENTION_DAYS}일)")
    db_path = Path(DB_FILE)
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        log.info(f"[DB] 현재 크기: {size_mb:.1f} MB")
        if size_mb > 500:
            log.warning(f"[DB] ⚠️ {size_mb:.1f} MB — 500MB 초과")
    con.close()

# ── 통계 로깅 헬퍼 ──────────────────────────────────────────────────────────
# _audience_open_id: camera → row id  (0=전체, 1=CAM1, 2=CAM2)
_audience_open_id: dict = {}   # camera → open event id
_heat_open: dict       = {}    # (robot_id, channel) → (id, state, ts_start)
_fault_prev: dict      = {}    # robot_id → fault (0/1)

def stats_audience_start(camera: int = 0):
    """관객 등장 이벤트 시작 (camera: 0=전체, 1=CAM1, 2=CAM2)"""
    try:
        con = sqlite3.connect(DB_FILE)
        cur = con.execute("INSERT INTO audience_events (ts_start, camera) VALUES (?,?)",
                          (time.time(), camera))
        _audience_open_id[camera] = cur.lastrowid
        con.commit(); con.close()
    except Exception as e:
        log.error(f"[STATS] audience_start cam={camera}: {e}")

def stats_audience_end(camera: int = 0):
    """관객 퇴장 이벤트 종료 (camera: 0=전체, 1=CAM1, 2=CAM2)"""
    eid = _audience_open_id.pop(camera, None)
    if eid is None:
        return
    try:
        now = time.time()
        con = sqlite3.connect(DB_FILE)
        con.execute("""
            UPDATE audience_events SET ts_end=?, duration_s=ts_end-ts_start
            WHERE id=? AND ts_end IS NULL
        """, (now, eid))
        con.commit(); con.close()
    except Exception as e:
        log.error(f"[STATS] audience_end cam={camera}: {e}")

def stats_heat_update(robot_id: int, state: str):
    """HEAT/SUST 상태 추적 — 채널 구분 없이 robot level로 기록"""
    now = time.time()
    # channel=1(s1 기준), IF는 채널 하나만 사용
    key = (robot_id, 1)
    prev = _heat_open.get(key)

    if state in ('HEAT', 'SUST'):
        if prev is None or prev[1] != state:
            # 새로운 상태 시작 → 이전 것 닫기
            if prev is not None:
                _close_heat(key, now)
            try:
                con = sqlite3.connect(DB_FILE)
                cur = con.execute("""
                    INSERT INTO heat_log (ts_start, robot_id, channel, state)
                    VALUES (?, ?, ?, ?)
                """, (now, robot_id, 1, state))
                _heat_open[key] = (cur.lastrowid, state, now)
                con.commit(); con.close()
            except Exception as e:
                log.error(f"[STATS] heat_start R{robot_id}: {e}")
    else:
        if prev is not None:
            _close_heat(key, now)

def _close_heat(key, ts_end: float):
    entry = _heat_open.pop(key, None)
    if entry is None:
        return
    eid, state, ts_start = entry
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""
            UPDATE heat_log SET ts_end=?, duration_s=?
            WHERE id=? AND ts_end IS NULL
        """, (ts_end, ts_end - ts_start, eid))
        con.commit(); con.close()
    except Exception as e:
        log.error(f"[STATS] heat_close: {e}")

def stats_fault_check(robot_id: int, fault: int, code: int):
    """Fault 신규 발생 시 기록"""
    prev = _fault_prev.get(robot_id, 0)
    if fault and not prev:
        try:
            con = sqlite3.connect(DB_FILE)
            con.execute("INSERT INTO fault_log (ts, robot_id, fault_code) VALUES (?,?,?)",
                        (time.time(), robot_id, code))
            con.commit(); con.close()
        except Exception as e:
            log.error(f"[STATS] fault R{robot_id}: {e}")
    _fault_prev[robot_id] = fault

def stats_sound_log(track: int, action: str):
    """사운드 이벤트 기록 (action: PLAY, STOP, LOOP)"""
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("INSERT INTO sound_log (ts, track, action) VALUES (?,?,?)",
                    (time.time(), track, action))
        con.commit(); con.close()
    except Exception as e:
        log.error(f"[STATS] sound: {e}")

def db_insert(p: dict, loss_pct: float, rate1: float, rate2: float):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""
            INSERT INTO packets
            (ts,robot_id,pkt_num,state,fault,code,s1,s2,amb,sw,elapsed,sw_el,tmax,tmin,mode,preset,loss_pct,rate1,rate2)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (time.time(), p['robot_id'], p['pkt_num'], p['state'],
              p['fault'], p['code'], p['s1'], p['s2'], p['amb'],
              p['sw'], p['elapsed'], p['sw_el'], p['tmax'], p['tmin'],
              p['mode'], p['preset'],
              loss_pct, rate1, rate2))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"[DB] {e}")


def db_cleanup():
    """오래된 DB 데이터 삭제 + VACUUM — 24시간마다 실행"""
    while True:
        time.sleep(86400)
        try:
            cutoff = time.time() - DB_RETENTION_DAYS * 86400
            con = sqlite3.connect(DB_FILE)
            cur = con.execute("DELETE FROM packets WHERE ts < ?", (cutoff,))
            deleted = cur.rowcount
            con.commit()
            con.execute("VACUUM")
            con.commit()
            con.close()
            size_mb = Path(DB_FILE).stat().st_size / (1024 * 1024) if Path(DB_FILE).exists() else 0
            log.info(f"[DB] 정리: {deleted}행 삭제 / 현재 {size_mb:.1f} MB")
        except Exception as e:
            log.error(f"[DB CLEANUP] {e}")


# ═══════════════════════════════════════════════════════
#  통계 HTTP API  (GET /api/stats?period=hour|day|week|month)
# ═══════════════════════════════════════════════════════
STATS_API_PORT = int(os.getenv('STATS_API_PORT', '8080'))

ROBOT_NAMES = {
    **{i: f'Golden Petals R{i:02d}' for i in range(1, 13)},
    **{i: f'Indet. Float R{i:02d}'  for i in range(13, 19)},
}

def _stats_query(period: str) -> dict:
    """period: hour | day | week | month — 현재 시각 기준 과거 N 단위"""
    now = time.time()
    spans = {'hour': 3600, 'day': 86400, 'week': 604800, 'month': 2592000}
    span  = spans.get(period, 86400)
    since = now - span

    # 버킷 크기 (초)
    bucket = {'hour': 300, 'day': 3600, 'week': 86400, 'month': 86400 * 3}[period]
    label_fmt = {'hour': '%H:%M', 'day': '%H시', 'week': '%m/%d', 'month': '%m/%d'}[period]

    try:
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row

        import datetime

        # ① 관람객 빈도 — 버킷별 방문 수 (카메라별 분리)
        rows = con.execute("""
            SELECT ts_start, camera FROM audience_events WHERE ts_start >= ?
        """, (since,)).fetchall()
        n_buckets      = int(span // bucket) + 1
        aud_buckets:   dict = {}
        cam1_buckets:  dict = {}
        cam2_buckets:  dict = {}
        for r in rows:
            b = int((r['ts_start'] - since) // bucket)
            cam = r['camera'] if r['camera'] is not None else 0
            if cam == 0:
                aud_buckets[b]  = aud_buckets.get(b, 0) + 1
            elif cam == 1:
                cam1_buckets[b] = cam1_buckets.get(b, 0) + 1
            elif cam == 2:
                cam2_buckets[b] = cam2_buckets.get(b, 0) + 1

        aud_labels   = []
        aud_counts   = []  # camera=0 (OR 합산)
        cam1_counts  = []
        cam2_counts  = []
        total_counts = []  # camera=0 우선, 없으면 cam1+cam2 중복제거로 fallback
        for i in range(n_buckets):
            t = datetime.datetime.fromtimestamp(since + i * bucket)
            aud_labels.append(t.strftime(label_fmt))
            c0  = aud_buckets.get(i, 0)
            c1  = cam1_buckets.get(i, 0)
            c2  = cam2_buckets.get(i, 0)
            aud_counts.append(c0)
            cam1_counts.append(c1)
            cam2_counts.append(c2)
            # 전체: camera=0이 있으면 그것을 사용, 없으면 max(cam1,cam2) 사용
            # (cam1과 cam2는 같은 공간에서 다른 각도 → 동일인물 중복 포함)
            total_counts.append(c0 if c0 > 0 else max(c1, c2))

        # ② 관람객 총계 (카메라별)
        total_visits  = con.execute("SELECT COUNT(*) FROM audience_events WHERE ts_start >= ? AND camera=0", (since,)).fetchone()[0]
        total_cam1    = con.execute("SELECT COUNT(*) FROM audience_events WHERE ts_start >= ? AND camera=1", (since,)).fetchone()[0]
        total_cam2    = con.execute("SELECT COUNT(*) FROM audience_events WHERE ts_start >= ? AND camera=2", (since,)).fetchone()[0]
        # 전체 방문 fallback: camera=0 없으면 max(cam1, cam2) — 같은 공간이므로 단순 합산하면 중복
        total_unified = total_visits if total_visits > 0 else max(total_cam1, total_cam2)
        avg_duration  = con.execute("SELECT AVG(duration_s) FROM audience_events WHERE ts_start >= ? AND camera=0 AND duration_s IS NOT NULL", (since,)).fetchone()[0] or 0
        avg_dur_cam1  = con.execute("SELECT AVG(duration_s) FROM audience_events WHERE ts_start >= ? AND camera=1 AND duration_s IS NOT NULL", (since,)).fetchone()[0] or 0
        avg_dur_cam2  = con.execute("SELECT AVG(duration_s) FROM audience_events WHERE ts_start >= ? AND camera=2 AND duration_s IS NOT NULL", (since,)).fetchone()[0] or 0

        # ③ 로봇별 가열 시간 합계 (HEAT + SUST) — packets 테이블에서 직접 계산
        # LEAD()로 상태 전환 시점을 찾아 사이클 종료시의 elapsed = 사이클 지속시간
        heat_rows = con.execute("""
            WITH heat_ends AS (
                SELECT robot_id, elapsed AS dur_s, ts AS last_ts
                FROM (
                    SELECT robot_id, ts, state, elapsed,
                           LEAD(state) OVER (PARTITION BY robot_id ORDER BY ts) AS nxt
                    FROM packets
                    WHERE robot_id BETWEEN 1 AND 18 AND ts >= ?
                ) WHERE state='HEAT' AND (nxt IS NULL OR nxt != 'HEAT')
            ),
            sust_ends AS (
                SELECT robot_id, elapsed AS dur_s
                FROM (
                    SELECT robot_id, ts, state, elapsed,
                           LEAD(state) OVER (PARTITION BY robot_id ORDER BY ts) AS nxt
                    FROM packets
                    WHERE robot_id BETWEEN 1 AND 18 AND ts >= ?
                ) WHERE state='SUST' AND (nxt IS NULL OR nxt != 'SUST')
            ),
            hc AS (
                SELECT robot_id,
                       COUNT(*) AS heat_cycles,
                       SUM(dur_s) AS heat_s,
                       AVG(dur_s) AS avg_heat_s,
                       MIN(dur_s) AS min_heat_s,
                       MAX(dur_s) AS max_heat_s,
                       MAX(last_ts) AS last_heat_ts
                FROM heat_ends GROUP BY robot_id
            ),
            sc AS (
                SELECT robot_id,
                       COUNT(*) AS sust_cycles,
                       SUM(dur_s) AS sust_s,
                       AVG(dur_s) AS avg_sust_s,
                       MIN(dur_s) AS min_sust_s,
                       MAX(dur_s) AS max_sust_s
                FROM sust_ends GROUP BY robot_id
            )
            SELECT COALESCE(h.robot_id, s.robot_id) AS robot_id,
                   COALESCE(h.heat_cycles, 0) AS heat_cycles,
                   COALESCE(h.heat_s, 0)      AS heat_s,
                   COALESCE(h.avg_heat_s, 0)  AS avg_heat_s,
                   COALESCE(h.min_heat_s, 0)  AS min_heat_s,
                   COALESCE(h.max_heat_s, 0)  AS max_heat_s,
                   COALESCE(h.last_heat_ts, 0) AS last_heat_ts,
                   COALESCE(s.sust_cycles, 0) AS sust_cycles,
                   COALESCE(s.sust_s, 0)      AS sust_s,
                   COALESCE(s.avg_sust_s, 0)  AS avg_sust_s,
                   COALESCE(s.min_sust_s, 0)  AS min_sust_s,
                   COALESCE(s.max_sust_s, 0)  AS max_sust_s
            FROM hc h LEFT JOIN sc s ON h.robot_id = s.robot_id
            UNION
            SELECT COALESCE(h.robot_id, s.robot_id) AS robot_id,
                   COALESCE(h.heat_cycles, 0) AS heat_cycles,
                   COALESCE(h.heat_s, 0)      AS heat_s,
                   COALESCE(h.avg_heat_s, 0)  AS avg_heat_s,
                   COALESCE(h.min_heat_s, 0)  AS min_heat_s,
                   COALESCE(h.max_heat_s, 0)  AS max_heat_s,
                   COALESCE(h.last_heat_ts, 0) AS last_heat_ts,
                   COALESCE(s.sust_cycles, 0) AS sust_cycles,
                   COALESCE(s.sust_s, 0)      AS sust_s,
                   COALESCE(s.avg_sust_s, 0)  AS avg_sust_s,
                   COALESCE(s.min_sust_s, 0)  AS min_sust_s,
                   COALESCE(s.max_sust_s, 0)  AS max_sust_s
            FROM sc s LEFT JOIN hc h ON h.robot_id = s.robot_id
            WHERE h.robot_id IS NULL
            ORDER BY robot_id
        """, (since, since)).fetchall()

        # ④ 가열 사이클 횟수 버킷 (전체 로봇 합산) — packets 테이블에서 cycle start 감지
        # HEAT 상태의 첫 번째 패킷(elapsed=1 또는 직전 state != HEAT)을 cycle start로 봄
        cycle_rows = con.execute("""
            SELECT ts
            FROM (
                SELECT ts, state, elapsed,
                       LAG(state) OVER (PARTITION BY robot_id ORDER BY ts) AS prev_state
                FROM packets
                WHERE robot_id BETWEEN 1 AND 18 AND ts >= ?
            )
            WHERE state='HEAT' AND (prev_state IS NULL OR prev_state != 'HEAT')
        """, (since,)).fetchall()
        cycle_buckets: dict = {}
        for r in cycle_rows:
            b = int((r['ts'] - since) // bucket)
            cycle_buckets[b] = cycle_buckets.get(b, 0) + 1
        cycle_counts = [cycle_buckets.get(i, 0) for i in range(n_buckets)]

        # ⑤ 최고 온도 통계 (로봇별) — heat_by_robot 병합용
        temp_rows = con.execute("""
            SELECT robot_id,
                   MAX(s1) AS max_s1, MAX(s2) AS max_s2,
                   AVG(s1) AS avg_s1, AVG(s2) AS avg_s2
            FROM packets
            WHERE ts >= ? AND state IN ('HEAT','SUST')
            GROUP BY robot_id ORDER BY robot_id
        """, (since,)).fetchall()
        temp_dict = {r['robot_id']: r for r in temp_rows}

        # heat_by_robot 조립 (온도 데이터 병합)
        heat_by_robot = []
        for r in heat_rows:
            rid = r['robot_id']
            t   = temp_dict.get(rid)
            heat_by_robot.append({
                'robot_id':    rid,
                'name':        ROBOT_NAMES.get(rid, f'R{rid:02d}'),
                'heat_s':      round(r['heat_s']      or 0, 1),
                'sust_s':      round(r['sust_s']      or 0, 1),
                'total_s':     round((r['heat_s'] or 0) + (r['sust_s'] or 0), 1),
                'heat_cycles': r['heat_cycles']  or 0,
                'sust_cycles': r['sust_cycles']  or 0,
                'avg_heat_s':  round(r['avg_heat_s']  or 0, 1),
                'min_heat_s':  round(r['min_heat_s']  or 0, 1),
                'max_heat_s':  round(r['max_heat_s']  or 0, 1),
                'avg_sust_s':  round(r['avg_sust_s']  or 0, 1),
                'min_sust_s':  round(r['min_sust_s']  or 0, 1),
                'max_sust_s':  round(r['max_sust_s']  or 0, 1),
                'last_heat_ts': r['last_heat_ts'] or 0,
                'max_s1':      round((t['max_s1'] if t else None) or 0, 1),
                'max_s2':      round((t['max_s2'] if t else None) or 0, 1),
                'avg_s1':      round((t['avg_s1'] if t else None) or 0, 1),
                'avg_s2':      round((t['avg_s2'] if t else None) or 0, 1),
            })

        temp_by_robot = [{'robot_id': r['robot_id'],
                          'max_s1': round(r['max_s1'] or 0, 1),
                          'max_s2': round(r['max_s2'] or 0, 1),
                          'avg_s1': round(r['avg_s1'] or 0, 1),
                          'avg_s2': round(r['avg_s2'] or 0, 1)} for r in temp_rows]

        # ⑥ Fault 이력
        fault_rows = con.execute("""
            SELECT robot_id, fault_code, COUNT(*) AS cnt
            FROM fault_log WHERE ts >= ?
            GROUP BY robot_id, fault_code ORDER BY cnt DESC
        """, (since,)).fetchall()
        fault_summary = [{'robot_id': r['robot_id'], 'code': r['fault_code'], 'count': r['cnt']} for r in fault_rows]

        # ⑦ 사운드 재생 통계
        sound_rows = con.execute("""
            SELECT track, action, COUNT(*) AS cnt
            FROM sound_log WHERE ts >= ?
            GROUP BY track, action ORDER BY track, action
        """, (since,)).fetchall()
        sound_summary = [{'track': r['track'], 'action': r['action'], 'count': r['cnt']} for r in sound_rows]

        con.close()

        return {
            'period': period,
            'since':  since,
            'now':    now,
            'audience': {
                'labels':         aud_labels,
                'counts':         aud_counts,
                'total_counts':   total_counts,   # 전체 (OR 합산)
                'total_visits':   total_unified,
                'avg_duration_s': round(avg_duration, 1),
                'cam1': {
                    'counts':         cam1_counts,
                    'total_visits':   total_cam1,
                    'avg_duration_s': round(avg_dur_cam1, 1),
                },
                'cam2': {
                    'counts':         cam2_counts,
                    'total_visits':   total_cam2,
                    'avg_duration_s': round(avg_dur_cam2, 1),
                },
            },
            'heat': {
                'labels':       aud_labels,
                'cycle_counts': cycle_counts,
                'by_robot':     heat_by_robot,
            },
            'temp':   temp_by_robot,
            'faults': fault_summary,
            'sound':  sound_summary,
        }
    except Exception as e:
        log.error(f"[STATS API] {e}")
        return {'error': str(e)}


from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class StatsHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 불필요한 access log 억제
        pass

    def do_GET(self):
        import urllib.parse as up
        import mimetypes
        parsed = up.urlparse(self.path)
        path   = parsed.path

        # ── /api/stats ──
        if path == '/api/stats':
            params = dict(up.parse_qsl(parsed.query))
            period = params.get('period', 'day')
            if period not in ('hour', 'day', 'week', 'month'):
                period = 'day'
            data = _stats_query(period)
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)
            return

        # ── /launch/light_gui (LIGHT DISABLED) ──
        # if path == '/launch/light_gui':
        #     import subprocess
        #     gui_path = Path(__file__).parent.parent.parent / 'light' / 'light_test_gui.py'
        #     try:
        #         subprocess.Popen(
        #             ['python3', str(gui_path)],
        #             start_new_session=True,
        #             stdout=subprocess.DEVNULL,
        #             stderr=subprocess.DEVNULL,
        #         )
        #         result = {'ok': True}
        #     except Exception as e:
        #         result = {'ok': False, 'error': str(e)}
        #     body = json.dumps(result).encode('utf-8')
        #     self.send_response(200)
        #     self.send_header('Content-Type', 'application/json; charset=utf-8')
        #     self.send_header('Content-Length', str(len(body)))
        #     self.send_header('Access-Control-Allow-Origin', '*')
        #     self.end_headers()
        #     self.wfile.write(body)
        #     return

        # ── 정적 파일 (HTML/JS/CSS 등) ──
        base_dir = Path(__file__).parent
        # / 또는 /index.html → sema_control.html
        if path in ('/', '/index.html', ''):
            path = '/sema_control.html'
        file_path = (base_dir / path.lstrip('/')).resolve()
        # 디렉토리 탈출 방지
        if not str(file_path).startswith(str(base_dir.resolve())):
            self.send_error(403); return
        if not file_path.exists():
            self.send_error(404); return
        mime, _ = mimetypes.guess_type(str(file_path))
        mime = mime or 'application/octet-stream'
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def stats_api_server():
    srv = ThreadingHTTPServer(('0.0.0.0', STATS_API_PORT), StatsHandler)
    log.info(f"[STATS] 대시보드+API 서버 on :{STATS_API_PORT}")
    log.info(f"[STATS]   대시보드: http://0.0.0.0:{STATS_API_PORT}/")
    log.info(f"[STATS]   통계 API:  http://0.0.0.0:{STATS_API_PORT}/api/stats?period=day")
    srv.serve_forever()


# ── MQTT 수신 audience 이벤트 추적 ─────────────────────────────────────────
_aud_present: dict = {0: False, 1: False, 2: False}  # camera → present

def handle_audience_mqtt(payload: str, camera: int = 0):
    """MQTT audience/present 토픽 처리 → 통계 기록
    camera: 0=전체(OR), 1=CAM1, 2=CAM2
    """
    present = payload.strip().lower() == 'true'
    prev    = _aud_present.get(camera, False)
    if present and not prev:
        stats_audience_start(camera)
    elif not present and prev:
        stats_audience_end(camera)
    _aud_present[camera] = present


# ═══════════════════════════════════════════════════════
#  Telegram
# ═══════════════════════════════════════════════════════
def telegram_send(msg: str):
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == 'YOUR_BOT_TOKEN':
        return
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({'chat_id': TELEGRAM_CHAT_ID, 'text': msg}).encode()
        urllib.request.urlopen(url, data, timeout=5)
        log.info(f"[TG] {msg}")
    except Exception as e:
        log.warning(f"[TG ERR] {e}")

def _tapo_reset_thread(group_id: int):
    """Tapo 전원 리셋 — 별도 스레드에서 실행"""
    name = TAPO_GROUP_NAMES.get(group_id, f'그룹{group_id}')
    ok   = reset_group_sync(group_id)
    telegram_send(f"{'✅' if ok else '❌'} {name} 전원 리셋 {'완료' if ok else '실패'}")

def _tapo_power_thread(group_id: int, on: bool):
    """Tapo 전원 ON/OFF — 별도 스레드에서 실행"""
    name  = TAPO_GROUP_NAMES.get(group_id, f'그룹{group_id}')
    label = 'ON' if on else 'OFF'
    ok    = set_group_power_sync(group_id, on)
    telegram_send(f"{'✅' if ok else '❌'} {name} 전원 {label} {'완료' if ok else '실패'}")


def handle_telegram_command(text: str, mqtt_client):
    """
    지원 명령:
      /reset <id>    → sma/robot/<id>/cmd 에 RESET 발행 → UDP 포워드
      /power <1~5>   → Tapo 스마트플러그 전원 리셋
      /power         → 그룹 목록 확인
      /status        → 현재 모든 로봇 last_seen 출력
      /help          → 명령 목록
    """
    parts = text.split()
    cmd   = parts[0].lower()

    if cmd == '/reset' and len(parts) == 2:
        rid   = parts[1]
        topic = f"sma/robot/{rid}/cmd"
        mqtt_client.publish(topic, 'RESET', qos=1)
        telegram_send(f"✅ /reset {rid} 명령 전송 → {topic}")
        log.info(f"[CMD] RESET → robot {rid}")

    elif cmd == '/power':
        if not TAPO_AVAILABLE:
            telegram_send("⚠️ tapo 패키지 미설치: pip install tapo")
            return
        if len(parts) == 1:
            lines = ["📋 Tapo 그룹 목록:"]
            for gid, name in TAPO_GROUP_NAMES.items():
                ip = TAPO_PLUG_MAP.get(gid)
                lines.append(f"  {gid}. {name}  {'✅' if ip else '⬜ 미설정'}")
            telegram_send('\n'.join(lines))
            return
        try:
            gid = int(parts[1])
        except ValueError:
            telegram_send("⚠️ 사용법: /power <1~5>")
            return
        if gid not in TAPO_GROUP_NAMES:
            telegram_send(f"⚠️ 그룹 번호는 1~5 (받은 값: {gid})")
            return
        if gid not in TAPO_PLUG_MAP:
            telegram_send(f"⚠️ TAPO_GROUP_{gid} 미설정 (.env)")
            return
        name = TAPO_GROUP_NAMES[gid]
        telegram_send(f"🔌 {name} 전원 리셋 시작...")
        threading.Thread(target=_tapo_reset_thread, args=(gid,), daemon=True).start()

    elif cmd == '/status':
        now   = time.time()
        lines = [f"R{rid}: {int(now - ts)}초 전" for rid, ts in last_seen.items()]
        telegram_send("📡 로봇 상태\n" + ('\n'.join(lines) if lines else '데이터 없음'))

    elif cmd == '/help':
        telegram_send("/reset <id> — 소프트웨어 리셋\n/power <1~5> — 전원 리셋 (Tapo)\n/power — 그룹 목록\n/status — 온라인 상태\n/help — 도움말")

def telegram_poll(mqtt_client):
    """Telegram long-polling으로 명령 수신"""
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == 'YOUR_BOT_TOKEN':
        return
    # 기존 webhook/session 해제 (409 Conflict 방지)
    try:
        close_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/close"
        urllib.request.urlopen(close_url, data=b'', timeout=5)
    except Exception:
        pass
    try:
        hook_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook"
        urllib.request.urlopen(hook_url, data=b'', timeout=5)
    except Exception:
        pass
    time.sleep(2)  # 서버 세션 정리 대기
    offset = 0
    backoff = 1
    while True:
        try:
            url = (f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
                   f"/getUpdates?timeout=10&offset={offset}")
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read())
            backoff = 1
            for upd in data.get('result', []):
                offset = upd['update_id'] + 1
                msg    = upd.get('message', {}).get('text', '').strip()
                if msg:
                    handle_telegram_command(msg, mqtt_client)
        except urllib.error.HTTPError as e:
            if e.code == 409:  # Conflict: 다른 polling 세션 존재
                log.warning(f"[TG POLL] 409 Conflict — {backoff}s 후 재시도")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
            else:
                log.warning(f"[TG POLL] HTTP {e.code}: {e}")
                time.sleep(backoff)
        except Exception as e:
            log.warning(f"[TG POLL] {e}")
            time.sleep(backoff)


# ═══════════════════════════════════════════════════════
#  패킷 손실률 계산
# ═══════════════════════════════════════════════════════
def update_loss(robot_id: int, pkt_num: int) -> float:
    if robot_id not in last_pkt_num:
        last_pkt_num[robot_id] = pkt_num
        loss_counter[robot_id] = {'expected': 0, 'lost': 0}
        return 0.0

    gap  = (pkt_num - last_pkt_num[robot_id]) & 0xFFFF
    if gap == 0:
        return 0.0
    # 재부팅 감지: Teensy pkt_num 리셋 → gap 매우 큼 (~0.5s/pkt 기준 gap>100 ≈ 50초)
    # 손실 카운터·업타임 모두 초기화하여 오경보 방지
    if gap > 100:
        last_pkt_num[robot_id] = pkt_num
        loss_counter[robot_id] = {'expected': 0, 'lost': 0}
        first_seen[robot_id]   = time.time()
        log.info(f"[LOSS] R{robot_id} 재부팅 감지 (gap={gap}) → 카운터 초기화")
        return 0.0
    lost = max(0, gap - 1)
    loss_counter[robot_id]['expected'] += gap
    loss_counter[robot_id]['lost']     += lost
    last_pkt_num[robot_id] = pkt_num

    total_exp  = loss_counter[robot_id]['expected']
    total_lost = loss_counter[robot_id]['lost']
    loss_pct   = total_lost / total_exp if total_exp else 0.0

    # 패킷 손실 알림 — 텔레그램 제외 (대시보드에서만 표시)
    alerted_loss[robot_id] = loss_pct >= LOSS_RATE_WARN

    return loss_pct


# ═══════════════════════════════════════════════════════
#  온도 변화율 계산
# ═══════════════════════════════════════════════════════
def update_temp_rate(robot_id: int, s1: float, s2: float) -> tuple:
    if robot_id not in temp_history:
        temp_history[robot_id] = deque()
    hist = temp_history[robot_id]
    now  = time.time()
    hist.append((now, s1, s2))
    while hist and now - hist[0][0] > TEMP_WINDOW * 2:
        hist.popleft()
    if len(hist) < 2:
        return 0.0, 0.0
    dt = hist[-1][0] - hist[0][0]
    if dt < 0.5:
        return 0.0, 0.0
    rate1 = (hist[-1][1] - hist[0][1]) / dt
    rate2 = (hist[-1][2] - hist[0][2]) / dt

    # 온도 급상승 알림 — 텔레그램 제외 (대시보드에서만 표시)
    alerted_temp_rate[robot_id] = max(rate1, rate2) >= TEMP_RATE_WARN

    return rate1, rate2


# ═══════════════════════════════════════════════════════
#  알림
# ═══════════════════════════════════════════════════════
def check_alerts(p: dict):
    rid   = p['robot_id']
    fault = p['fault']
    state = p['state']
    s1    = p['s1']
    s2    = p['s2']
    code  = p['code']
    tmax  = p['tmax']

    # fault 알림
    if fault and not alerted_fault.get(rid):
        alerted_fault[rid] = True
        if code == 99:
            telegram_send(f"🔴 R{rid} I2C 동결! 자동 리셋 중\nstate={state} S1={s1:.1f}° S2={s2:.1f}°")
        else:
            telegram_send(f"🚨 R{rid} 센서 오류!\nstate={state} code={code}\nS1={s1:.1f}° S2={s2:.1f}°")
    if not fault:
        alerted_fault[rid] = False

    # 통계 기록
    stats_fault_check(rid, fault, code)
    stats_heat_update(rid, state)

    # 상태 전환 — 텔레그램 제외 (정상 동작이므로)
    prev_state[rid] = state

def check_offline():
    while True:
        now = time.time()
        for rid, ts in list(last_seen.items()):
            since = now - ts
            if since >= OFFLINE_TIMEOUT and not alerted_offline.get(rid):
                alerted_offline[rid] = True
                telegram_send(f"📡 R{rid} 응답 없음\n{int(since)}초째 신호 없음")
            if since < 5:
                alerted_offline[rid] = False
        time.sleep(5)


def cmd_ack_monitor(client):
    """CMD ACK 타임아웃 감지 — 2초 간격 폴링, 단일 백그라운드 스레드"""
    while True:
        time.sleep(2)
        now = time.time()
        for rid in list(pending_cmd.keys()):
            info = pending_cmd.get(rid)
            if info and now - info['sent_ts'] >= CMD_ACK_TIMEOUT:
                pending_cmd.pop(rid, None)
                log.warning(f"[CMD ACK] R{rid} TIMEOUT cmd={info['payload']}")
                client.publish(f"sma/robot/{rid}/ack",
                               f"TIMEOUT:{info['payload']}", qos=1)


# ═══════════════════════════════════════════════════════
#  XBee 패킷 파싱
# ═══════════════════════════════════════════════════════
def parse_xbee_packet(data: bytes) -> list:
    results = []
    buf = list(data)
    i   = 0
    while i <= len(buf) - PKT_LEN:
        if buf[i] == START_B and buf[i + PKT_LEN - 1] == END_B:
            pkt        = buf[i:i+PKT_LEN]
            robot_id   = pkt[1] - 50
            fault      = pkt[2]
            fault_code = pkt[3]
            temp1      = (pkt[4] * 253 + pkt[5]) / 100.0
            temp2      = (pkt[6] * 253 + pkt[7]) / 100.0
            state      = STATE_NAMES.get(pkt[8], '?')
            elapsed    = pkt[9] * 253 + pkt[10]
            sw         = pkt[11]
            state2     = STATE_NAMES.get(pkt[11], '?')  # GP: Unit2 state / IF: switch(0/1)
            sw_el      = pkt[12] * 253 + pkt[13]
            amb        = (pkt[14] * 253 + pkt[15]) / 100.0
            pkt_num    = pkt[16] * 253 + pkt[17]
            tmax       = pkt[18]
            tmin       = pkt[19]
            mode       = pkt[20]
            preset     = pkt[21]
            results.append({
                'robot_id': robot_id,
                'pkt_num' : pkt_num,
                'fault'   : fault,
                'code'    : fault_code,
                'state'   : state,
                'elapsed' : elapsed,
                's1'      : temp1,
                's2'      : temp2,
                'amb'     : amb,
                'sw'      : sw,
                'state2'  : state2,
                'sw_el'   : sw_el,
                'tmax'    : tmax,
                'tmin'    : tmin,
                'mode'    : mode,
                'preset'  : preset,
            })
            i += PKT_LEN
        else:
            i += 1
    return results


# ═══════════════════════════════════════════════════════
#  MQTT 콜백
# ═══════════════════════════════════════════════════════
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        log.info(f"[MQTT] Connected to {BROKER}")
        client.subscribe("sma/robot/+/cmd", qos=1)
        client.subscribe("sma/power/group/+", qos=1)
        client.subscribe("sma/cue",           qos=0)
        client.subscribe("audience/present",      qos=0)
        client.subscribe("audience/cam1/present", qos=0)
        client.subscribe("audience/cam2/present", qos=0)
    else:
        log.error(f"[MQTT] Connect failed rc={rc}")

def on_disconnect(client, userdata, rc, properties=None, reasoncode=None):
    log.warning(f"[MQTT] Disconnected (rc={rc})")

def on_message(client, userdata, message):
    """MQTT 메시지 처리: robot cmd → UDP 포워드, power group → Tapo 제어"""
    try:
        topic   = message.topic
        payload = message.payload.decode('utf-8', errors='replace').strip()
        parts   = topic.split('/')

        # ── 관람객 감지 통계 ───────────────────────────────────
        if topic == 'audience/present':
            handle_audience_mqtt(payload, camera=0)
            return
        if topic == 'audience/cam1/present':
            handle_audience_mqtt(payload, camera=1)
            return
        if topic == 'audience/cam2/present':
            handle_audience_mqtt(payload, camera=2)
            return

        if len(parts) == 4 and parts[0] == 'sma' and parts[1] == 'power' and parts[2] == 'group':
            try:
                group_id = int(parts[3])
            except ValueError:
                log.warning(f"[POWER] invalid group id: {parts[3]}")
                return
            if payload == 'on':
                log.info(f"[POWER] group {group_id} → ON")
                threading.Thread(target=_tapo_power_thread, args=(group_id, True), daemon=True).start()
            elif payload == 'off':
                log.info(f"[POWER] group {group_id} → OFF")
                threading.Thread(target=_tapo_power_thread, args=(group_id, False), daemon=True).start()
            else:  # reset (default)
                log.info(f"[POWER] group {group_id} → RESET")
                threading.Thread(target=_tapo_reset_thread, args=(group_id,), daemon=True).start()
            return

        # ── Pd 큐: sma/cue ────────────────────────────────────────────
        if topic == 'sma/cue':
            try:
                data = json.loads(payload)
                ip   = str(data.get('ip', '')).strip()
                port = int(data.get('port', 3000))
                val  = int(data.get('val', 1)) & 0xFF
                if ip:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                        s.sendto(bytes([val]), (ip, port))
                    log.info(f"[PD CUE] → {ip}:{port}  byte=0x{val:02X} ({val})")
                else:
                    log.warning("[PD CUE] IP 없음 — 전송 생략")
            except Exception as e:
                log.error(f"[PD CUE ERR] {e}")
            return

        # ── robot cmd → UDP 포워드: sma/robot/<id>/cmd ───────────────
        rid = parts[2] if len(parts) >= 3 else 'unknown'
        log.info(f"[MQTT→UDP] {topic} | {payload}")
        fwd_msg = f"{rid};{payload}\n".encode('utf-8')
        fwd_sock.sendto(fwd_msg, (UDP_FORWARD_IP, UDP_FORWARD_PORT))
        # 사운드 커맨드 통계 기록
        if payload.startswith('SOUND:'):
            parts2 = payload.split(':')
            try:
                track = int(parts2[1])
                loop  = len(parts2) > 2 and parts2[2] == '1'
                action = 'STOP' if track == 0 else ('LOOP' if loop else 'PLAY')
                stats_sound_log(track, action)
            except (IndexError, ValueError):
                pass
        # CMD ACK 추적 시작
        try:
            rid_int = int(rid)
            pending_cmd[rid_int] = {'payload': payload, 'sent_ts': time.time()}
            client.publish(f"sma/robot/{rid}/ack", f"SENT:{payload}", qos=1)
            log.info(f"[CMD ACK] R{rid} 추적 시작 (timeout={CMD_ACK_TIMEOUT}s)")
        except (ValueError, AttributeError):
            pass
    except Exception as e:
        log.error(f"[MQTT→UDP ERR] {e}")


# ═══════════════════════════════════════════════════════
#  MQTT 능동 재연결
# ═══════════════════════════════════════════════════════
def mqtt_reconnect_loop(client):
    backoff = 5
    while True:
        time.sleep(backoff)
        if not client.is_connected():
            log.info(f"[MQTT] 재연결 시도... (backoff={backoff}s)")
            try:
                client.reconnect()
                backoff = 5
            except Exception as e:
                log.warning(f"[MQTT] 재연결 실패: {e}")
                backoff = min(backoff * 2, 60)


# ═══════════════════════════════════════════════════════
#  UDP 리스너 (XBee → MQTT)
# ═══════════════════════════════════════════════════════
def udp_listener(client):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((UDP_LISTEN_IP, UDP_PORT))
    log.info(f"[UDP] Listening on {UDP_LISTEN_IP}:{UDP_PORT}")
    log.info(f"[UDP] Forward → {UDP_FORWARD_IP}:{UDP_FORWARD_PORT}")

    while True:
        try:
            data, addr = sock.recvfrom(4096)
            packets    = parse_xbee_packet(data)

            if packets:
                for p in packets:
                    rid            = p['robot_id']
                    last_seen[rid] = time.time()
                    # CMD ACK 확인 — 로봇에서 패킷이 오면 명령 수신 확인
                    if rid in pending_cmd:
                        info = pending_cmd.pop(rid)
                        elapsed_ack = time.time() - info['sent_ts']
                        log.info(f"[CMD ACK] R{rid} OK ({elapsed_ack:.1f}s) cmd={info['payload']}")
                        client.publish(f"sma/robot/{rid}/ack",
                                       f"OK:{info['payload']}", qos=1)
                    if rid not in first_seen:
                        first_seen[rid] = time.time()
                    uptime = p['pkt_num'] // 2  # pkt_num × 0.5s ≈ 로봇 부팅 후 경과 시간(초)

                    loss_pct     = update_loss(rid, p['pkt_num'])
                    rate1, rate2 = update_temp_rate(rid, p['s1'], p['s2'])
                    check_alerts(p)
                    db_insert(p, loss_pct, rate1, rate2)

                    text = (
                        f"{rid};state={p['state']};state2={p['state2']};s1={p['s1']:.1f};s2={p['s2']:.1f}"
                        f";amb={p['amb']:.1f};fault={p['fault']};code={p['code']}"
                        f";el={p['elapsed']};sw={p['sw']};swel={p['sw_el']}"
                        f";pkt={p['pkt_num']};loss={loss_pct*100:.1f}"
                        f";rate1={rate1:.2f};rate2={rate2:.2f}"
                        f";tmax={p['tmax']};tmin={p['tmin']}"
                        f";mode={p['mode']};preset={p['preset']}"
                        f";uptime={uptime}"
                    )
                    topic = f"sma/robot/{rid}/log"
                    log.info(f"[UDP→MQTT] R{rid} | {text}")
                    client.publish(topic, text + '\n', qos=0, retain=True)
            else:
                # 텍스트 폴백 (테스트용 nc 명령 등)
                text = data.decode('utf-8', errors='replace').strip()
                if not text:
                    continue
                try:
                    robot_id = text.split(';')[0].strip()
                    int(robot_id)
                except (IndexError, ValueError):
                    robot_id = 'unknown'
                topic = f"sma/robot/{robot_id}/log"
                log.info(f"[UDP→MQTT fallback] {addr[0]} → {topic} | {text}")
                client.publish(topic, text + '\n', qos=0)

        except Exception as e:
            log.error(f"[UDP ERR] {e}")


# ═══════════════════════════════════════════════════════
#  메인
# ═══════════════════════════════════════════════════════
def main():
    db_init()

    client_id = f"sma_bridge_{uuid.uuid4().hex[:8]}"
    log.info(f"[MQTT] client_id={client_id}")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message
    if MQTT_TLS:
        client.tls_set()  # 시스템 CA 인증서 사용 (HiveMQ Cloud, EMQX Cloud 등)
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
        log.info(f"[MQTT] 인증: user={MQTT_USER}")
    client.connect(BROKER, BROKER_PORT, keepalive=60)
    client.loop_start()

    threading.Thread(target=udp_listener,       args=(client,), daemon=True).start()
    threading.Thread(target=check_offline,                      daemon=True).start()
    threading.Thread(target=mqtt_reconnect_loop, args=(client,), daemon=True).start()
    threading.Thread(target=db_cleanup,                          daemon=True).start()
    threading.Thread(target=cmd_ack_monitor,     args=(client,), daemon=True).start()
    threading.Thread(target=telegram_poll,       args=(client,), daemon=True).start()
    threading.Thread(target=stats_api_server,                    daemon=True).start()

    log.info("[BRIDGE] SMA Bridge v2 running. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("[BRIDGE] Stopped.")
        client.loop_stop()
        sys.exit(0)

if __name__ == '__main__':
    main()
