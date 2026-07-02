#!/usr/bin/env python3
"""
debug_sound.py — 사운드 경로 단계별 디버그 도구
================================================
사용:
  python3 debug_sound.py            # 대화형 메뉴
  python3 debug_sound.py send 2 1   # robot 2 에 SOUND:1 전송
  python3 debug_sound.py send 2 0   # robot 2 에 SOUND:0 (정지)
  python3 debug_sound.py raw 17     # Serial2 TX(pin17)로 직접 바이트 17 전송 (오디오실드 직결)
  python3 debug_sound.py monitor    # sema_host 로그 실시간 감시
"""
import sys, socket, time, threading, os

UDP_FORWARD_IP   = '127.0.0.1'
UDP_FORWARD_PORT = 5006            # sema_host.py CMD 수신 포트
LOG_FILE = os.path.join(os.path.dirname(__file__),
                        '../host/logs/sema_host.log')

# ── UDP 전송 ─────────────────────────────────────────────────────────────
def send_sound(robot_id: int, snd: int):
    """sema_host.py에 UDP로 'robot_id;SOUND:snd' 전달"""
    msg = f"{robot_id};SOUND:{snd}\n".encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(msg, (UDP_FORWARD_IP, UDP_FORWARD_PORT))
    sock.close()
    print(f"[→ UDP] {UDP_FORWARD_IP}:{UDP_FORWARD_PORT}  msg={msg.strip()!r}")

# ── 로그 실시간 감시 ─────────────────────────────────────────────────────
def tail_log(path: str, stop_evt: threading.Event):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            f.seek(0, 2)          # 파일 끝으로 이동
            while not stop_evt.is_set():
                line = f.readline()
                if line:
                    print('[LOG]', line, end='')
                else:
                    time.sleep(0.1)
    except FileNotFoundError:
        print(f"[ERR] 로그 파일 없음: {path}")

# ── 오디오실드 직접 바이트 전송 ──────────────────────────────────────────
def raw_serial(byte_val: int, port: str = None, baud: int = 9600):
    """Audio Shield Teensy 의 Serial1 RX(pin0)에 직접 바이트 전송.
    port: /dev/ttyACM0 등 오디오실드 USB 시리얼 포트 (없으면 자동 탐색)
    """
    import serial, glob
    if port is None:
        candidates = glob.glob('/dev/ttyACM*')
        if not candidates:
            print("[ERR] /dev/ttyACM* 장치 없음 — 오디오실드 USB 연결 확인")
            return
        port = candidates[0]
        print(f"[INFO] 자동 선택: {port}")
    ser = serial.Serial(port, baud, timeout=1)
    time.sleep(0.3)
    ser.write(bytes([byte_val]))
    print(f"[→ Serial] {port} @ {baud}bps  byte=0x{byte_val:02X} ({byte_val})")
    # 응답(있다면) 읽기
    time.sleep(0.5)
    out = ser.read(ser.in_waiting or 1)
    if out:
        print(f"[← Serial] {out}")
    ser.close()

# ── 대화형 메뉴 ──────────────────────────────────────────────────────────
def interactive():
    print("═" * 55)
    print("  SeMA 사운드 디버그 도구")
    print("═" * 55)
    print("[1] SOUND:1 전송 (로봇2 → 오디오실드 트랙1 once)")
    print("[2] SOUND:2 전송 (로봇2 → 오디오실드 트랙1 loop)")
    print("[0] SOUND:0 전송 (정지)")
    print("[r] 오디오실드 USB 직접 바이트 전송")
    print("[m] sema_host 로그 감시 시작")
    print("[q] 종료")
    print()

    stop = threading.Event()
    monitor_thread = None

    while True:
        choice = input("선택 > ").strip().lower()

        if choice in ('1', '2', '0'):
            snd = int(choice)
            stop_mon = threading.Event()
            t = threading.Thread(target=tail_log, args=(LOG_FILE, stop_mon), daemon=True)
            t.start()
            print(f"[INFO] 로그 감시 시작 — SOUND:{snd} 전송 중...")
            time.sleep(0.1)
            send_sound(2, snd)
            time.sleep(1.5)
            stop_mon.set()
            t.join(timeout=2)
            print()

        elif choice == 'r':
            v = input("전송할 바이트 값 (0~8): ").strip()
            p = input("시리얼 포트 (비우면 자동탐색): ").strip() or None
            try:
                raw_serial(int(v), p)
            except Exception as e:
                print(f"[ERR] {e}")

        elif choice == 'm':
            if monitor_thread and monitor_thread.is_alive():
                print("[INFO] 이미 감시 중")
                continue
            stop = threading.Event()
            monitor_thread = threading.Thread(target=tail_log, args=(LOG_FILE, stop), daemon=True)
            monitor_thread.start()
            print("[INFO] 로그 감시 중... (q 입력 시 종료)")

        elif choice == 'q':
            stop.set()
            break

# ── 메인 ─────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    args = sys.argv[1:]

    if not args:
        interactive()

    elif args[0] == 'send' and len(args) == 3:
        send_sound(int(args[1]), int(args[2]))

    elif args[0] == 'raw' and len(args) >= 2:
        port = args[2] if len(args) > 2 else None
        raw_serial(int(args[1]), port)

    elif args[0] == 'monitor':
        stop = threading.Event()
        try:
            tail_log(LOG_FILE, stop)
        except KeyboardInterrupt:
            stop.set()
    else:
        print(__doc__)
