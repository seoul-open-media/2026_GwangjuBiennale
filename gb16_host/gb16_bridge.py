#!/usr/bin/env python3
"""
gb16_bridge.py  —  GB16 2026 광주비엔날레  UDP(XBee) ↔ MQTT Bridge
v1.0  (bridge.py 기반)

흐름 (수신): [로봇 XBee] → [gb16_host.py] --UDP:6005--> [이 스크립트] --MQTT--> [원격 모니터]
흐름 (송신): [dashboard/Telegram] → [MQTT cmd] → [이 스크립트] --UDP:6006--> [gb16_host.py]

설치: pip install paho-mqtt python-dotenv
실행: python gb16_bridge.py

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
  b12 = swElapsed MSB, b13 = swElapsed LSB
  b14 = amb MSB, b15 = amb LSB
  b16 = pkt_num MSB, b17 = pkt_num LSB
  b18 = tempTarget
  b19 = targetTempMin
  b20 = mode (1=user_defined 2=play_preset)
  b21 = presetNum
  b22 = 254 (END)

MQTT 토픽 구조:
  gb16/robot/<id>/log     — 로봇 상태 (retain)
  gb16/robot/<id>/cmd     — 로봇 명령 (구독)
  gb16/robot/<id>/ack     — 명령 응답
  gb16/power/group/<id>   — 전원 제어 (구독)
  gb16/cue                — PD 큐 트리거 (구독)
  audience/present        — 관람객 감지 (구독)
  audience/cam1/present   — 카메라1 관람객 감지 (구독)
  audience/cam2/present   — 카메라2 관람객 감지 (구독)
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
import random
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
UDP_PORT         = int(os.getenv('UDP_PORT',     '6005'))
UDP_FORWARD_IP   = os.getenv('UDP_FORWARD_IP',   '127.0.0.1')
UDP_FORWARD_PORT = int(os.getenv('UDP_FORWARD_PORT', '6006'))

BROKER           = os.getenv('MQTT_BROKER',      'broker.hivemq.com')
BROKER_PORT      = int(os.getenv('MQTT_PORT',    '1883'))
MQTT_USER        = os.getenv('MQTT_USER',        '')
MQTT_PASS        = os.getenv('MQTT_PASS',        '')
MQTT_TLS         = os.getenv('MQTT_TLS',         '')

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_TOKEN',   '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

LOG_FILE         = os.getenv('LOG_FILE',         'gb16_bridge.log')
DB_FILE          = os.getenv('DB_FILE',          'gb16_data.db')
STATIC_DIR       = os.getenv('STATIC_DIR',       str(Path(__file__).parent))

# 관객 감지 상태를 Pure Data PC로 UDP 전달
AUDIENCE_PD_UDP_IP    = os.getenv('AUDIENCE_PD_UDP_IP', '192.168.0.12').strip()
AUDIENCE_PD_UDP_PORT  = int(os.getenv('AUDIENCE_PD_UDP_PORT', '7001'))
AUDIENCE_PD_UDP_ENABLE = os.getenv('AUDIENCE_PD_UDP_ENABLE', '1').strip().lower() in ('1', 'true', 'yes', 'on')

TAPO_PLUG_MAP: dict = {}
for _k, _v in os.environ.items():
    if _k.startswith('TAPO_GROUP_'):
        try: TAPO_PLUG_MAP[int(_k[len('TAPO_GROUP_'):])] = _v.strip()
        except ValueError: pass
TAPO_GROUP_NAMES: dict = {
    1: 'IF + Bellshaker + Light1 (192.168.0.100)',
    2: 'Domino (192.168.0.101)',
    3: 'IF Ceiling + Light2 (192.168.0.102)',
}

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

TEMP_RATE_WARN   = 2.0
TEMP_WINDOW      = 10
LOSS_RATE_WARN   = 0.10
OFFLINE_TIMEOUT  = 15
CMD_ACK_TIMEOUT  = 10
DB_RETENTION_DAYS = int(os.getenv('DB_RETENTION_DAYS', '7'))

# ─── MQTT 토픽 프리픽스 ───────────────────────────────
TOPIC_PREFIX = os.getenv('MQTT_TOPIC_PREFIX', 'gb16')

# ─── 상태 저장 ────────────────────────────────────────
alerted_fault     = {}
alerted_offline   = {}
alerted_loss      = {}
alerted_temp_rate = {}
last_seen         = {}
first_seen        = {}
last_pkt_num      = {}
loss_counter      = {}
temp_history      = {}
prev_state        = {}
pending_cmd       = {}

fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
aud_udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


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
    con.execute("""
        CREATE TABLE IF NOT EXISTS audience_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_start    REAL NOT NULL,
            ts_end      REAL,
            duration_s  REAL,
            camera      INTEGER NOT NULL DEFAULT 0
        )
    """)
    try:
        con.execute("ALTER TABLE audience_events ADD COLUMN camera INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
    con.execute("""
        CREATE TABLE IF NOT EXISTS heat_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_start    REAL NOT NULL,
            ts_end      REAL,
            robot_id    INTEGER NOT NULL,
            channel     INTEGER NOT NULL,
            state       TEXT NOT NULL,
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
            action      TEXT NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_audience_start ON audience_events(ts_start)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_heat_robot     ON heat_log(robot_id, ts_start)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_fault_ts       ON fault_log(ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sound_ts       ON sound_log(ts)")
    con.commit()
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


_audience_open_id: dict = {}
_heat_open: dict       = {}
_fault_prev: dict      = {}

def stats_audience_start(camera: int = 0):
    try:
        con = sqlite3.connect(DB_FILE)
        cur = con.execute("INSERT INTO audience_events (ts_start, camera) VALUES (?,?)",
                          (time.time(), camera))
        _audience_open_id[camera] = cur.lastrowid
        con.commit(); con.close()
    except Exception as e:
        log.error(f"[STATS] audience_start cam={camera}: {e}")

def stats_audience_end(camera: int = 0):
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
    now = time.time()
    key = (robot_id, 1)
    prev = _heat_open.get(key)
    if state in ('HEAT', 'SUST'):
        if prev is None or prev[1] != state:
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
STATS_API_PORT = int(os.getenv('STATS_API_PORT', '8182'))
CUSTOM_TIMELINE_FILE = Path(os.getenv('CUSTOM_TIMELINE_FILE', str(Path(STATIC_DIR) / 'custom_timeline.json')))
CUSTOM_TIMELINE_BAK_FILE = Path(os.getenv('CUSTOM_TIMELINE_BAK_FILE', str(Path(STATIC_DIR) / 'custom_timeline.bak.json')))
PD_CUE_FILE = Path(os.getenv('PD_CUE_FILE', str(Path(STATIC_DIR) / 'pd_cue.json')))

ROBOT_NAMES = {
    **{i: f'Indet. Float R{i:02d}'  for i in range(1,  31)},
    **{i: f'IF Ceiling R{i:02d}'    for i in range(31, 35)},
}

def _stats_query(period: str) -> dict:
    now = time.time()
    spans = {'hour': 3600, 'day': 86400, 'week': 604800, 'month': 2592000}
    span  = spans.get(period, 86400)
    since = now - span
    bucket = {'hour': 300, 'day': 3600, 'week': 86400, 'month': 86400 * 3}[period]
    label_fmt = {'hour': '%H:%M', 'day': '%H시', 'week': '%m/%d', 'month': '%m/%d'}[period]

    try:
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        import datetime

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
        aud_counts   = []
        cam1_counts  = []
        cam2_counts  = []
        total_counts = []
        for i in range(n_buckets):
            t = datetime.datetime.fromtimestamp(since + i * bucket)
            aud_labels.append(t.strftime(label_fmt))
            c0  = aud_buckets.get(i, 0)
            c1  = cam1_buckets.get(i, 0)
            c2  = cam2_buckets.get(i, 0)
            aud_counts.append(c0)
            cam1_counts.append(c1)
            cam2_counts.append(c2)
            total_counts.append(c0 if c0 > 0 else max(c1, c2))

        total_visits  = con.execute("SELECT COUNT(*) FROM audience_events WHERE ts_start >= ? AND camera=0", (since,)).fetchone()[0]
        total_cam1    = con.execute("SELECT COUNT(*) FROM audience_events WHERE ts_start >= ? AND camera=1", (since,)).fetchone()[0]
        total_cam2    = con.execute("SELECT COUNT(*) FROM audience_events WHERE ts_start >= ? AND camera=2", (since,)).fetchone()[0]
        total_unified = total_visits if total_visits > 0 else max(total_cam1, total_cam2)
        avg_duration  = con.execute("SELECT AVG(duration_s) FROM audience_events WHERE ts_start >= ? AND camera=0 AND duration_s IS NOT NULL", (since,)).fetchone()[0] or 0
        avg_dur_cam1  = con.execute("SELECT AVG(duration_s) FROM audience_events WHERE ts_start >= ? AND camera=1 AND duration_s IS NOT NULL", (since,)).fetchone()[0] or 0
        avg_dur_cam2  = con.execute("SELECT AVG(duration_s) FROM audience_events WHERE ts_start >= ? AND camera=2 AND duration_s IS NOT NULL", (since,)).fetchone()[0] or 0

        heat_rows = con.execute("""
            WITH heat_ends AS (
                SELECT robot_id, elapsed AS dur_s, ts AS last_ts
                FROM (
                    SELECT robot_id, ts, state, elapsed,
                           LEAD(state) OVER (PARTITION BY robot_id ORDER BY ts) AS nxt
                    FROM packets
                    WHERE robot_id BETWEEN 1 AND 34 AND ts >= ?
                ) WHERE state='HEAT' AND (nxt IS NULL OR nxt != 'HEAT')
            ),
            sust_ends AS (
                SELECT robot_id, elapsed AS dur_s
                FROM (
                    SELECT robot_id, ts, state, elapsed,
                           LEAD(state) OVER (PARTITION BY robot_id ORDER BY ts) AS nxt
                    FROM packets
                    WHERE robot_id BETWEEN 1 AND 34 AND ts >= ?
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
                   COALESCE(h.heat_cycles, 0), COALESCE(h.heat_s, 0),
                   COALESCE(h.avg_heat_s, 0), COALESCE(h.min_heat_s, 0),
                   COALESCE(h.max_heat_s, 0), COALESCE(h.last_heat_ts, 0),
                   COALESCE(s.sust_cycles, 0), COALESCE(s.sust_s, 0),
                   COALESCE(s.avg_sust_s, 0), COALESCE(s.min_sust_s, 0),
                   COALESCE(s.max_sust_s, 0)
            FROM sc s LEFT JOIN hc h ON h.robot_id = s.robot_id
            WHERE h.robot_id IS NULL
            ORDER BY robot_id
        """, (since, since)).fetchall()

        cycle_rows = con.execute("""
            SELECT ts
            FROM (
                SELECT ts, state,
                       LAG(state) OVER (PARTITION BY robot_id ORDER BY ts) AS prev_state
                FROM packets
                WHERE robot_id BETWEEN 1 AND 34 AND ts >= ?
            )
            WHERE state='HEAT' AND (prev_state IS NULL OR prev_state != 'HEAT')
        """, (since,)).fetchall()
        cycle_buckets: dict = {}
        for r in cycle_rows:
            b = int((r['ts'] - since) // bucket)
            cycle_buckets[b] = cycle_buckets.get(b, 0) + 1
        cycle_counts = [cycle_buckets.get(i, 0) for i in range(n_buckets)]

        temp_rows = con.execute("""
            SELECT robot_id,
                   MAX(s1) AS max_s1, MAX(s2) AS max_s2,
                   AVG(s1) AS avg_s1, AVG(s2) AS avg_s2
            FROM packets
            WHERE ts >= ? AND state IN ('HEAT','SUST')
            GROUP BY robot_id ORDER BY robot_id
        """, (since,)).fetchall()
        temp_dict = {r['robot_id']: r for r in temp_rows}

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

        fault_rows = con.execute("""
            SELECT robot_id, fault_code, COUNT(*) AS cnt
            FROM fault_log WHERE ts >= ?
            GROUP BY robot_id, fault_code ORDER BY cnt DESC
        """, (since,)).fetchall()
        fault_summary = [{'robot_id': r['robot_id'], 'code': r['fault_code'], 'count': r['cnt']} for r in fault_rows]

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
                'total_counts':   total_counts,
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


def _read_custom_timeline() -> dict:
    """서버에 저장된 커스텀 타임라인을 읽어 반환한다."""
    def _parse_timeline_text(raw: str) -> dict:
        data = json.loads(raw)
        if isinstance(data, dict):
            steps = data.get('steps')
            if isinstance(steps, list):
                return {
                    'savedAt': data.get('savedAt'),
                    'steps': steps,
                }
        if isinstance(data, list):
            return {'steps': data}
        return {'steps': []}

    try:
        if CUSTOM_TIMELINE_FILE.exists():
            parsed = _parse_timeline_text(CUSTOM_TIMELINE_FILE.read_text(encoding='utf-8'))
            if parsed.get('steps'):
                return parsed
        if CUSTOM_TIMELINE_BAK_FILE.exists():
            parsed_bak = _parse_timeline_text(CUSTOM_TIMELINE_BAK_FILE.read_text(encoding='utf-8'))
            if parsed_bak.get('steps'):
                return parsed_bak
    except Exception as e:
        log.warning(f"[CUSTOM TIMELINE] read failed: {e}")
    return {'steps': []}


def _write_custom_timeline(payload: dict) -> None:
    """커스텀 타임라인을 파일에 원자적으로 저장한다."""
    steps = payload.get('steps') if isinstance(payload, dict) else None
    if not isinstance(steps, list):
        raise ValueError('steps must be a list')
    if len(steps) > 1000:
        raise ValueError('steps too many')

    record = {
        'savedAt': int(time.time() * 1000),
        'steps': steps,
    }

    CUSTOM_TIMELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_TIMELINE_BAK_FILE.parent.mkdir(parents=True, exist_ok=True)

    # 직전 정상본을 백업으로 보관해 예기치 않은 덮어쓰기/손상 시 복구한다.
    if CUSTOM_TIMELINE_FILE.exists():
        try:
            CUSTOM_TIMELINE_BAK_FILE.write_text(CUSTOM_TIMELINE_FILE.read_text(encoding='utf-8'), encoding='utf-8')
        except Exception as e:
            log.warning(f"[CUSTOM TIMELINE] backup rotate failed: {e}")

    tmp = CUSTOM_TIMELINE_FILE.with_suffix(CUSTOM_TIMELINE_FILE.suffix + '.tmp')
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, CUSTOM_TIMELINE_FILE)

    # 최신 정상본도 별도 백업 파일에 유지한다.
    CUSTOM_TIMELINE_BAK_FILE.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')


def _read_pd_cue() -> dict:
    """서버에 저장된 Pd 큐 설정을 읽어 반환한다."""
    try:
        if not PD_CUE_FILE.exists():
            return {'ip': '', 'port': 3000, 'val': 1}
        data = json.loads(PD_CUE_FILE.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return {'ip': '', 'port': 3000, 'val': 1}
        ip = str(data.get('ip', '')).strip()
        port_raw = data.get('port', 3000)
        val_raw = data.get('val', 1)
        try:
            port = int(port_raw)
        except Exception:
            port = 3000
        try:
            val = int(val_raw)
        except Exception:
            val = 1
        return {
            'ip': ip,
            'port': max(1, min(65535, port)),
            'val': max(0, min(255, val)),
        }
    except Exception as e:
        log.warning(f"[PD CUE] read failed: {e}")
        return {'ip': '', 'port': 3000, 'val': 1}


def _write_pd_cue(payload: dict) -> None:
    """Pd 큐 설정을 파일에 원자적으로 저장한다."""
    if not isinstance(payload, dict):
        raise ValueError('payload must be an object')
    ip = str(payload.get('ip', '')).strip()
    try:
        port = int(payload.get('port', 3000))
    except Exception:
        port = 3000
    try:
        val = int(payload.get('val', 1))
    except Exception:
        val = 1

    record = {
        'savedAt': int(time.time() * 1000),
        'ip': ip,
        'port': max(1, min(65535, port)),
        'val': max(0, min(255, val)),
    }

    PD_CUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PD_CUE_FILE.with_suffix(PD_CUE_FILE.suffix + '.tmp')
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, PD_CUE_FILE)


from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class StatsHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        import urllib.parse as up
        parsed = up.urlparse(self.path)
        path = parsed.path

        if path not in ('/api/custom_timeline', '/api/pd_cue'):
            self.send_error(404)
            return

        try:
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(max(0, length)) if length > 0 else b'{}'
            data = json.loads(raw.decode('utf-8')) if raw else {}
            if path == '/api/custom_timeline':
                _write_custom_timeline(data)
            else:
                _write_pd_cue(data)
            self._send_json(200, {'ok': True})
        except ValueError as e:
            self._send_json(400, {'ok': False, 'error': str(e)})
        except Exception as e:
            if path == '/api/custom_timeline':
                log.error(f"[CUSTOM TIMELINE] save failed: {e}")
            else:
                log.error(f"[PD CUE] save failed: {e}")
            self._send_json(500, {'ok': False, 'error': 'save_failed'})

    def do_GET(self):
        import urllib.parse as up
        import mimetypes
        parsed = up.urlparse(self.path)
        path   = parsed.path

        if path == '/api/stats':
            params = dict(up.parse_qsl(parsed.query))
            period = params.get('period', 'day')
            if period not in ('hour', 'day', 'week', 'month'):
                period = 'day'
            data = _stats_query(period)
            self._send_json(200, data)
            return

        if path == '/api/custom_timeline':
            self._send_json(200, _read_custom_timeline())
            return

        if path == '/api/pd_cue':
            self._send_json(200, _read_pd_cue())
            return

        base_dir = Path(STATIC_DIR)
        # / 또는 /index.html → gb16_control.html (없으면 dashboard.html로 폴백)
        if path in ('/', '/index.html', ''):
            ctrl = base_dir / 'gb16_control.html'
            path = '/gb16_control.html' if ctrl.exists() else '/dashboard.html'
        file_path = (base_dir / path.lstrip('/')).resolve()
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
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.end_headers()
        self.wfile.write(body)

def stats_api_server():
    while True:
        try:
            srv = ThreadingHTTPServer(('0.0.0.0', STATS_API_PORT), StatsHandler)
            log.info(f"[STATS] 대시보드+API 서버 on :{STATS_API_PORT}")
            log.info(f"[STATS]   대시보드: http://0.0.0.0:{STATS_API_PORT}/")
            log.info(f"[STATS]   통계 API:  http://0.0.0.0:{STATS_API_PORT}/api/stats?period=day")
            srv.serve_forever()
        except OSError as e:
            log.warning(f"[STATS] bind 실패({e}) — 2초 후 재시도")
            time.sleep(2)
        except Exception as e:
            log.error(f"[STATS] 서버 오류({e}) — 2초 후 재시도")
            time.sleep(2)


_aud_present: dict = {0: False, 1: False, 2: False}


def _send_audience_to_pd(present: bool, heartbeat: bool = False):
    if not AUDIENCE_PD_UDP_ENABLE or not AUDIENCE_PD_UDP_IP:
        return
    try:
        # PD의 netreceive -u -b (바이너리 모드)는 수신 바이트를 그대로 float로
        # 내보내므로, ASCII 문자 '1'/'0'(=49/48)이 아니라 실제 바이트 값 1/0을 보낸다.
        aud_udp_sock.sendto(bytes([1 if present else 0]), (AUDIENCE_PD_UDP_IP, AUDIENCE_PD_UDP_PORT))
        if heartbeat:
            log.debug(f"[AUD->PD] heartbeat {'1' if present else '0'} to {AUDIENCE_PD_UDP_IP}:{AUDIENCE_PD_UDP_PORT}")
        else:
            log.info(f"[AUD->PD] sent {'1' if present else '0'} to {AUDIENCE_PD_UDP_IP}:{AUDIENCE_PD_UDP_PORT}")
    except Exception as e:
        log.error(f"[AUD->PD] udp send failed: {e}")

def handle_audience_mqtt(payload: str, camera: int = 0):
    present = payload.strip().lower() == 'true'
    prev    = _aud_present.get(camera, False)
    if present and not prev:
        stats_audience_start(camera)
    elif not present and prev:
        stats_audience_end(camera)
    _aud_present[camera] = present

    # 통합 토픽(audience/present) 상태 변화 시 PD로 1/0 즉시 전달
    if camera == 0 and present != prev:
        _send_audience_to_pd(present)


def audience_pd_heartbeat_loop():
    """UDP는 전달 보장이 없으므로, 상태 변화 엣지에서 1회만 보내면
    그 패킷이 유실되거나 PD가 그 순간 리슨 중이 아닐 때 영영 상태를
    놓칠 수 있다. 주기적으로 현재 상태를 재전송해 자동으로 동기화되게 한다."""
    if not AUDIENCE_PD_UDP_ENABLE or not AUDIENCE_PD_UDP_IP:
        return
    tick = 0
    while True:
        time.sleep(5)
        tick += 1
        # 5초마다 조용히 재전송, 60초에 한 번은 INFO로 살아있음을 남김
        _send_audience_to_pd(_aud_present.get(0, False), heartbeat=(tick % 12 != 0))


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
    name = TAPO_GROUP_NAMES.get(group_id, f'그룹{group_id}')
    log.info(f"[POWER] reset start group={group_id} name={name}")
    ok   = reset_group_sync(group_id)
    log.info(f"[POWER] reset {'ok' if ok else 'fail'} group={group_id} name={name}")
    telegram_send(f"{'✅' if ok else '❌'} {name} 전원 리셋 {'완료' if ok else '실패'}")

def _tapo_power_thread(group_id: int, on: bool):
    name  = TAPO_GROUP_NAMES.get(group_id, f'그룹{group_id}')
    label = 'ON' if on else 'OFF'
    log.info(f"[POWER] {label} start group={group_id} name={name}")
    ok    = set_group_power_sync(group_id, on)
    log.info(f"[POWER] {label} {'ok' if ok else 'fail'} group={group_id} name={name}")
    telegram_send(f"{'✅' if ok else '❌'} {name} 전원 {label} {'완료' if ok else '실패'}")


def handle_telegram_command(text: str, mqtt_client):
    """
    지원 명령:
      /reset <id>    → gb16/robot/<id>/cmd 에 RESET 발행 → UDP 포워드
      /power <1~5>   → Tapo 전원 리셋
      /status        → 로봇 last_seen
      /help          → 명령 목록
    """
    parts = text.split()
    cmd   = parts[0].lower()

    if cmd == '/reset' and len(parts) == 2:
        rid   = parts[1]
        topic = f"{TOPIC_PREFIX}/robot/{rid}/cmd"
        mqtt_client.publish(topic, 'RESET', qos=1)
        telegram_send(f"✅ /reset {rid} 명령 전송 → {topic}")

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
            telegram_send("⚠️ 사용법: /power <1~3>")
            return
        if gid not in TAPO_GROUP_NAMES:
            telegram_send(f"⚠️ 그룹 번호는 1~3 (받은 값: {gid})")
            return
        name = TAPO_GROUP_NAMES[gid]
        telegram_send(f"🔌 {name} 전원 리셋 시작...")
        threading.Thread(target=_tapo_reset_thread, args=(gid,), daemon=True).start()

    elif cmd == '/status':
        now   = time.time()
        lines = [f"R{rid}: {int(now - ts)}초 전" for rid, ts in last_seen.items()]
        telegram_send("📡 로봇 상태\n" + ('\n'.join(lines) if lines else '데이터 없음'))

    elif cmd == '/help':
        telegram_send("/reset <id> — 소프트웨어 리셋\n/power <1~5> — 전원 리셋 (Tapo)\n/status — 온라인 상태\n/help — 도움말")

def telegram_poll(mqtt_client):
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == 'YOUR_BOT_TOKEN':
        return
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
    time.sleep(2)
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
            if e.code == 409:
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
    alerted_temp_rate[robot_id] = max(abs(rate1), abs(rate2)) >= TEMP_RATE_WARN
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

    if fault and not alerted_fault.get(rid):
        alerted_fault[rid] = True
        if code == 99:
            telegram_send(f"🔴 R{rid} I2C 동결! 자동 리셋 중\nstate={state} S1={s1:.1f}° S2={s2:.1f}°")
        else:
            telegram_send(f"🚨 R{rid} 센서 오류!\nstate={state} code={code}\nS1={s1:.1f}° S2={s2:.1f}°")
    if not fault:
        alerted_fault[rid] = False

    stats_fault_check(rid, fault, code)
    stats_heat_update(rid, state)
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
    while True:
        time.sleep(2)
        now = time.time()
        for rid in list(pending_cmd.keys()):
            info = pending_cmd.get(rid)
            if info and now - info['sent_ts'] >= CMD_ACK_TIMEOUT:
                pending_cmd.pop(rid, None)
                log.warning(f"[CMD ACK] R{rid} TIMEOUT cmd={info['payload']}")
                client.publish(f"{TOPIC_PREFIX}/robot/{rid}/ack",
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
            state2     = state   # IF는 단일 상태 — pkt[11]은 switch(0/1), state 아님
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
#  스태거드 열 시작 스케줄러
# ═══════════════════════════════════════════════════════
IF_STAGGER_IDS = list(range(1, 31))   # 스태거드 대상 IF R01~R30

_stagger_stop   = threading.Event()
_stagger_thread: threading.Thread | None = None


def _build_balanced_groups(ids: list, group_size: int) -> list:
    """id 목록을 group_size개의 구간(zone)으로 나눠 zone별로 순서를 섞은 뒤,
    그룹마다 zone별로 하나씩 뽑아 구성한다 — 특정 구간(예: 1~5번대)에
    쏠리지 않고 전체 범위(1~30)에서 균형있게 선택되도록 하기 위함.
    예) 1~30, group_size=5 → 1~6 / 7~12 / 13~18 / 19~24 / 25~30 5개 구간에서
        각 그룹이 구간마다 1대씩 총 5대로 구성된다."""
    if not ids:
        return []
    group_size = max(1, group_size)
    n_zones = min(group_size, len(ids))
    zone_len = -(-len(ids) // n_zones)  # ceil division
    zones = []
    for i in range(n_zones):
        zone = ids[i * zone_len:(i + 1) * zone_len]
        random.shuffle(zone)
        zones.append(zone)
    total_groups = max((len(z) for z in zones), default=0)
    groups = []
    for gi in range(total_groups):
        group = [zone[gi] for zone in zones if gi < len(zone)]
        random.shuffle(group)  # 그룹 내부 순서도 랜덤화
        groups.append(group)
    return groups


def _run_stagger(ids: list, interval_s: int, group_size: int,
                 fan_speed: int | None, heat_pwm: int | None, heat_temp: int | None,
                 repeat: bool, cycle_delay_s: int, client) -> None:
    """구간별 균형 배분(각 그룹이 1~30 전 범위에서 고르게 뽑히도록)으로 group_size개씩
    interval_s초 간격으로 IF HEAT_ON 전송."""
    log.info(
        f"[STAGGER] 시작 — {len(ids)}대 / {interval_s}s 간격"
        f" / group={group_size} / repeat={'on' if repeat else 'off'}"
        f" / cycle={cycle_delay_s}s"
        f" / fan={fan_speed if fan_speed is not None else 'keep'}"
        f" / pwm={heat_pwm if heat_pwm is not None else 'keep'}"
        f" / temp={heat_temp if heat_temp is not None else 'keep'}"
    )
    cycle_index = 0
    while not _stagger_stop.is_set():
        cycle_index += 1
        groups = _build_balanced_groups(ids, group_size)
        n = len(groups)
        client.publish(
            f"{TOPIC_PREFIX}/stagger/status",
            json.dumps({"event": "start", "cycle": cycle_index, "total_groups": n,
                        "interval_s": interval_s, "group_size": group_size,
                        "repeat": repeat, "cycle_delay_s": cycle_delay_s,
                        "fan": fan_speed, "pwm": heat_pwm, "temp": heat_temp}),
            qos=0,
        )

        for i, group in enumerate(groups):
            if _stagger_stop.is_set():
                log.info(f"[STAGGER] 중단 — cycle {cycle_index} 그룹 {i + 1}/{n} 직전")
                client.publish(
                    f"{TOPIC_PREFIX}/stagger/status",
                    json.dumps({"event": "stopped", "cycle": cycle_index,
                                "group": i + 1, "total": n}),
                    qos=0,
                )
                return
            for rid in group:
                if fan_speed is not None or heat_pwm is not None or heat_temp is not None:
                    set_parts = []
                    if fan_speed is not None:
                        set_parts.append(f"fan={fan_speed}")
                    if heat_pwm is not None:
                        set_parts.append(f"pwm={heat_pwm}")
                    if heat_temp is not None:
                        set_parts.append(f"temp={heat_temp}")
                    fwd_sock.sendto(
                        f"{rid};SET {' '.join(set_parts)}\n".encode(),
                        (UDP_FORWARD_IP, UDP_FORWARD_PORT),
                    )
                fwd_sock.sendto(
                    f"{rid};HEAT_ON\n".encode(),
                    (UDP_FORWARD_IP, UDP_FORWARD_PORT),
                )
            log.info(f"[STAGGER] cycle {cycle_index} 그룹 {i + 1}/{n}: {group} → HEAT_ON")
            client.publish(
                f"{TOPIC_PREFIX}/stagger/status",
                json.dumps({"event": "group", "cycle": cycle_index,
                            "group": i + 1, "total": n, "ids": group}),
                qos=0,
            )
            if i < n - 1:
                _stagger_stop.wait(interval_s)   # 인터럽트 가능한 sleep

        if not repeat:
            log.info("[STAGGER] 완료")
            client.publish(
                f"{TOPIC_PREFIX}/stagger/status",
                json.dumps({"event": "done", "cycle": cycle_index, "total_groups": n}),
                qos=0,
            )
            return

        log.info(f"[STAGGER] cycle {cycle_index} 완료 — 다음 cycle 대기 {cycle_delay_s}s")
        client.publish(
            f"{TOPIC_PREFIX}/stagger/status",
            json.dumps({"event": "cycle_done", "cycle": cycle_index,
                        "next_in_s": cycle_delay_s}),
            qos=0,
        )
        if _stagger_stop.wait(cycle_delay_s):
            log.info(f"[STAGGER] 중단 — cycle {cycle_index} 완료 후")
            client.publish(
                f"{TOPIC_PREFIX}/stagger/status",
                json.dumps({"event": "stopped", "cycle": cycle_index, "group": n, "total": n}),
                qos=0,
            )
            return


# ═══════════════════════════════════════════════════════
#  MQTT 콜백
# ═══════════════════════════════════════════════════════
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        log.info(f"[MQTT] Connected to {BROKER}")
        client.subscribe(f"{TOPIC_PREFIX}/robot/+/cmd", qos=1)
        client.subscribe(f"{TOPIC_PREFIX}/power/group/+", qos=1)
        client.subscribe(f"{TOPIC_PREFIX}/cue",           qos=0)
        client.subscribe(f"{TOPIC_PREFIX}/all/cmd",       qos=1)
        client.subscribe("audience/present",      qos=0)
        client.subscribe("audience/cam1/present", qos=0)
        client.subscribe("audience/cam2/present", qos=0)
    else:
        log.error(f"[MQTT] Connect failed rc={rc}")

def on_disconnect(client, userdata, rc, properties=None, reasoncode=None):
    log.warning(f"[MQTT] Disconnected (rc={rc})")

def on_message(client, userdata, message):
    try:
        topic   = message.topic
        payload = message.payload.decode('utf-8', errors='replace').strip()
        parts   = topic.split('/')

        if topic == 'audience/present':
            handle_audience_mqtt(payload, camera=0)
            return
        if topic == 'audience/cam1/present':
            handle_audience_mqtt(payload, camera=1)
            return
        if topic == 'audience/cam2/present':
            handle_audience_mqtt(payload, camera=2)
            return

        if len(parts) == 4 and parts[0] == TOPIC_PREFIX and parts[1] == 'power' and parts[2] == 'group':
            try:
                group_id = int(parts[3])
            except ValueError:
                log.warning(f"[POWER] invalid group id: {parts[3]}")
                return
            log.info(f"[POWER] mqtt topic={topic} payload={payload!r}")
            if payload == 'on':
                threading.Thread(target=_tapo_power_thread, args=(group_id, True), daemon=True).start()
            elif payload == 'off':
                threading.Thread(target=_tapo_power_thread, args=(group_id, False), daemon=True).start()
            else:
                threading.Thread(target=_tapo_reset_thread, args=(group_id,), daemon=True).start()
            return

        if topic == f"{TOPIC_PREFIX}/all/cmd":
            global _stagger_thread, _stagger_stop
            upper = payload.upper()
            if upper.startswith('STAGGER_HEAT'):
                toks = payload.split()
                interval, group_size = 20, 5
                repeat, cycle_delay = False, 10
                fan_speed, heat_pwm, heat_temp = None, None, None
                for tok in toks[1:]:
                    k, _, v = tok.lower().partition('=')
                    try:
                        if k == 'interval': interval   = max(1, int(v))
                        elif k == 'group':  group_size = max(1, int(v))
                        elif k in ('repeat', 'loop'):
                            repeat = v in ('1', 'true', 'on', 'yes', 'y')
                        elif k == 'cycle':
                            cycle_delay = max(0, min(600, int(v)))
                        elif k == 'fan':    fan_speed  = max(0, min(255, int(v)))
                        elif k == 'pwm':    heat_pwm   = max(0, min(100, int(v)))
                        elif k == 'temp':   heat_temp  = max(40, min(50, int(v)))
                    except ValueError:
                        pass
                _stagger_stop.set()
                if _stagger_thread and _stagger_thread.is_alive():
                    _stagger_thread.join(timeout=0.5)
                _stagger_stop.clear()
                _stagger_thread = threading.Thread(
                    target=_run_stagger,
                      args=(IF_STAGGER_IDS, interval, group_size, fan_speed, heat_pwm, heat_temp,
                          repeat, cycle_delay, client),
                    daemon=True,
                )
                _stagger_thread.start()
                log.info(
                    f"[STAGGER] 요청 interval={interval}s group={group_size}"
                    f" repeat={'on' if repeat else 'off'} cycle={cycle_delay}s"
                    f" fan={fan_speed if fan_speed is not None else 'keep'}"
                    f" pwm={heat_pwm if heat_pwm is not None else 'keep'}"
                    f" temp={heat_temp if heat_temp is not None else 'keep'}"
                )
            elif upper == 'STAGGER_STOP':
                _stagger_stop.set()
                log.info("[STAGGER] 중단 요청")
            return

        if topic == f"{TOPIC_PREFIX}/cue":
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

        # robot cmd → UDP 포워드: gb16/robot/<id>/cmd
        rid = parts[2] if len(parts) >= 3 else 'unknown'
        log.info(f"[MQTT→UDP] {topic} | {payload}")
        fwd_msg = f"{rid};{payload}\n".encode('utf-8')
        fwd_sock.sendto(fwd_msg, (UDP_FORWARD_IP, UDP_FORWARD_PORT))

        if payload.startswith('SOUND:'):
            parts2 = payload.split(':')
            try:
                track = int(parts2[1])
                loop  = len(parts2) > 2 and parts2[2] == '1'
                action = 'STOP' if track == 0 else ('LOOP' if loop else 'PLAY')
                stats_sound_log(track, action)
            except (IndexError, ValueError):
                pass

        try:
            rid_int = int(rid)
            pending_cmd[rid_int] = {'payload': payload, 'sent_ts': time.time()}
            client.publish(f"{TOPIC_PREFIX}/robot/{rid}/ack", f"SENT:{payload}", qos=1)
        except (ValueError, AttributeError):
            pass
    except Exception as e:
        log.error(f"[MQTT→UDP ERR] {e}")


# ═══════════════════════════════════════════════════════
#  MQTT 재연결
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
                    if rid in pending_cmd:
                        info = pending_cmd.pop(rid)
                        elapsed_ack = time.time() - info['sent_ts']
                        log.info(f"[CMD ACK] R{rid} OK ({elapsed_ack:.1f}s) cmd={info['payload']}")
                        client.publish(f"{TOPIC_PREFIX}/robot/{rid}/ack",
                                       f"OK:{info['payload']}", qos=1)
                    if rid not in first_seen:
                        first_seen[rid] = time.time()
                    uptime = p['pkt_num'] // 2

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
                        f";uptime={uptime};ts={int(time.time())}"
                    )
                    topic = f"{TOPIC_PREFIX}/robot/{rid}/log"
                    log.info(f"[UDP→MQTT] R{rid} | {text}")
                    client.publish(topic, text + '\n', qos=0, retain=True)
            else:
                text = data.decode('utf-8', errors='replace').strip()
                if not text:
                    continue
                try:
                    robot_id = text.split(';')[0].strip()
                    int(robot_id)
                except (IndexError, ValueError):
                    robot_id = 'unknown'
                topic = f"{TOPIC_PREFIX}/robot/{robot_id}/log"
                log.info(f"[UDP→MQTT fallback] {addr[0]} → {topic} | {text}")
                client.publish(topic, text + '\n', qos=0)

        except Exception as e:
            log.error(f"[UDP ERR] {e}")


# ═══════════════════════════════════════════════════════
#  메인
# ═══════════════════════════════════════════════════════
def main():
    db_init()

    client_id = f"gb16_bridge_{uuid.uuid4().hex[:8]}"
    log.info(f"[MQTT] client_id={client_id}")
    log.info(f"[MQTT] topic prefix={TOPIC_PREFIX}")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message
    if MQTT_TLS:
        client.tls_set()
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
        log.info(f"[MQTT] 인증: user={MQTT_USER}")
    client.connect(BROKER, BROKER_PORT, keepalive=60)
    client.loop_start()

    threading.Thread(target=udp_listener,        args=(client,), daemon=True).start()
    threading.Thread(target=check_offline,                        daemon=True).start()
    threading.Thread(target=mqtt_reconnect_loop,  args=(client,), daemon=True).start()
    threading.Thread(target=db_cleanup,                           daemon=True).start()
    threading.Thread(target=cmd_ack_monitor,      args=(client,), daemon=True).start()
    threading.Thread(target=telegram_poll,        args=(client,), daemon=True).start()
    threading.Thread(target=stats_api_server,                     daemon=True).start()
    threading.Thread(target=audience_pd_heartbeat_loop,            daemon=True).start()

    log.info("[BRIDGE] GB16 Bridge v1 running. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("[BRIDGE] Stopped.")
        client.loop_stop()
        sys.exit(0)

if __name__ == '__main__':
    main()
