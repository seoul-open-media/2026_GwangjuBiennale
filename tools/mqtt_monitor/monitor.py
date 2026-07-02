#!/usr/bin/env python3
"""
SMA Controller - MQTT Monitor
모니터링 PC (원격)에서 실행

설치: pip install paho-mqtt
실행: python monitor.py
       python monitor.py 16        ← 특정 로봇만
       python monitor.py 16 17 18  ← 여러 로봇
"""

import paho.mqtt.client as mqtt
import sys
import time
from datetime import datetime

# ─── 설정 ─────────────────────────────────────────────
BROKER      = 'broker.hivemq.com'
BROKER_PORT = 1883

# 모니터링할 로봇 ID (커맨드라인 인자로 덮어쓰기 가능)
DEFAULT_IDS = [16, 17, 18]
# ──────────────────────────────────────────────────────

# 색상 (터미널 ANSI)
COLORS = {
    13: '\033[96m',  # cyan
    14: '\033[92m',  # green
    15: '\033[93m',  # yellow
    16: '\033[91m',  # red
    17: '\033[95m',  # magenta
    18: '\033[94m',  # blue
}
RESET = '\033[0m'

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"[MQTT] Connected to {BROKER}")
        for robot_id in userdata['ids']:
            topic = f"sma/robot/{robot_id}/log"
            client.subscribe(topic, qos=0)
            print(f"[SUB] {topic}")
    else:
        print(f"[MQTT] Connect failed rc={rc}")

def on_message(client, userdata, msg):
    topic = msg.topic
    robot_id = int(topic.split('/')[2])
    color = COLORS.get(robot_id, '')
    text = msg.payload.decode('utf-8', errors='replace').rstrip()
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"{color}[{ts}][R{robot_id}] {text}{RESET}")

def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(f"[MQTT] Disconnected unexpectedly (rc={rc}), reconnecting...")

def main():
    ids = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else DEFAULT_IDS
    print(f"[MONITOR] Watching robots: {ids}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sma_monitor", clean_session=True, userdata={'ids': ids})
    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    client.connect(BROKER, BROKER_PORT, keepalive=60)

    print("[MONITOR] Running. Ctrl+C to stop.")
    try:
        client.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        print("\n[MONITOR] Stopped.")

if __name__ == '__main__':
    main()
