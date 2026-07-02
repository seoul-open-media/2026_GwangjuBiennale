#!/usr/bin/env python3
"""
SeMA 2026 North — 카메라 MJPEG 스트림 서버

동작:
  - RTSP 카메라 2대 영상을 MJPEG 스트림으로 제공
  - MJPEG 스트림: http://localhost:8181/stream.mjpeg (좌우 합성)
                  http://localhost:8181/stream1.mjpeg (CAM1)
                  http://localhost:8181/stream2.mjpeg (CAM2)
  - 대시보드/제어 패널 HTML 정적 파일 서빙

실행:
  python3 audience_detector.py
"""

import time
import logging
import sys
import os
import threading
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import urllib.parse

import cv2
from dotenv import load_dotenv

# ─── 경로 ─────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent.resolve()
HOST_DIR    = SCRIPT_DIR.parent.parent / 'host'
LOG_DIR     = HOST_DIR / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ─── 환경변수 ──────────────────────────────────────────
load_dotenv(SCRIPT_DIR / '.env')
_cam_user    = os.getenv('TAPO_CAM_USER', 'admin')
_cam_pass    = urllib.parse.quote(os.getenv('TAPO_CAM_PASS', ''), safe='')
_cam1_ip     = os.getenv('AUDIENCE_CAMERA_1_IP', '')
_cam2_ip     = os.getenv('AUDIENCE_CAMERA_2_IP', '')
CAMERA_1_URL = f'rtsp://{_cam_user}:{_cam_pass}@{_cam1_ip}/stream2'
CAMERA_2_URL = f'rtsp://{_cam_user}:{_cam_pass}@{_cam2_ip}/stream2'
STREAM_PORT  = int(os.getenv('AUDIENCE_STREAM_PORT', '8181'))

# ─── 로깅 ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CAMERA] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / 'audience.log', encoding='utf-8'),
    ]
)
log = logging.getLogger(__name__)


_frame_lock   = threading.Lock()
_jpeg_frame   = b''  # 합성 (CAM1 + CAM2 좌우)
_jpeg_frame_1 = b''  # CAM1 개별
_jpeg_frame_2 = b''  # CAM2 개별

class _MJPEGHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args): pass  # access log 억제

    def do_GET(self):
        path = self.path.split('?')[0]   # 쿼리스트링 제거

        if path in ('/stream.mjpeg', '/stream1.mjpeg', '/stream2.mjpeg'):
            buf_map = {
                '/stream.mjpeg':  lambda: _jpeg_frame,
                '/stream1.mjpeg': lambda: _jpeg_frame_1,
                '/stream2.mjpeg': lambda: _jpeg_frame_2,
            }
            get_buf = buf_map[path]
            self.send_response(200)
            self.send_header('Content-Type',
                             'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('X-Accel-Buffering', 'no')          # 프록시 버퍼링 비활성화
            self.send_header('ngrok-skip-browser-warning', 'true')# ngrok 인터스티셜 우회
            self.send_header('Connection', 'close')
            self.end_headers()
            try:
                while True:
                    with _frame_lock:
                        data = get_buf()
                    if data:
                        self.wfile.write(
                            b'--frame\r\n'
                            b'Content-Type: image/jpeg\r\n\r\n' + data + b'\r\n'
                        )
                        self.wfile.flush()
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError):
                pass

        elif path in ('/snapshot.jpeg', '/snapshot1.jpeg', '/snapshot2.jpeg'):
            # 단일 JPEG 스냅샷 (프록시 친화적 대안)
            snap_map = {
                '/snapshot.jpeg':  lambda: _jpeg_frame,
                '/snapshot1.jpeg': lambda: _jpeg_frame_1,
                '/snapshot2.jpeg': lambda: _jpeg_frame_2,
            }
            with _frame_lock:
                data = snap_map[path]()
            if data:
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-cache, no-store')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('X-Accel-Buffering', 'no')
                self.send_header('ngrok-skip-browser-warning', 'true')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(503)
                self.send_header('Connection', 'close')
                self.end_headers()

        elif path in ('/', '/dashboard.html', '/sema_control.html'):
            # 정적 HTML 파일 서빙
            filename = 'dashboard.html' if path in ('/', '/dashboard.html') else path.lstrip('/')
            html_path = SCRIPT_DIR / filename
            try:
                content = html_path.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self.send_header('ngrok-skip-browser-warning', 'true')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_response(404)
                self.send_header('Connection', 'close')
                self.end_headers()

        elif path == '/mqtt.min.js':
            # mqtt.js 로컬 번들 서빙
            js_path = SCRIPT_DIR / 'mqtt.min.js'
            try:
                content = js_path.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'application/javascript; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'public, max-age=86400')
                self.send_header('ngrok-skip-browser-warning', 'true')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_response(404)
                self.send_header('Connection', 'close')
                self.end_headers()

        # ── /launch/light_gui (LIGHT DISABLED) ──────────────────────────
        # elif path == '/launch/light_gui':
        #     # 조명 테스트 GUI 실행
        #     gui_path = SCRIPT_DIR.parent.parent / 'light' / 'light_test_gui.py'
        #     try:
        #         subprocess.Popen(
        #             [sys.executable, str(gui_path)],
        #             cwd=str(gui_path.parent),
        #             start_new_session=True
        #         )
        #         body = json.dumps({'ok': True}).encode()
        #         self.send_response(200)
        #         self.send_header('Content-Type', 'application/json')
        #         self.send_header('Content-Length', str(len(body)))
        #         self.send_header('Access-Control-Allow-Origin', '*')
        #         self.send_header('Connection', 'close')
        #         self.end_headers()
        #         self.wfile.write(body)
        #     except Exception as e:
        #         body = json.dumps({'ok': False, 'error': str(e)}).encode()
        #         self.send_response(500)
        #         self.send_header('Content-Type', 'application/json')
        #         self.send_header('Content-Length', str(len(body)))
        #         self.send_header('Connection', 'close')
        #         self.end_headers()
        #         self.wfile.write(body)

        elif path == '/api/stats':
            # /api/stats 를 bridge.py 의 통계 API (포트 8080)로 프록시
            import urllib.request as _ureq
            try:
                proxy_url = f'http://localhost:8080{self.path}'
                with _ureq.urlopen(proxy_url, timeout=10) as resp:
                    body = resp.read()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('ngrok-skip-browser-warning', 'true')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                body = json.dumps({'error': str(e)}).encode()
                self.send_response(503)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)

        else:
            self.send_response(404)
            self.send_header('Connection', 'close')
            self.end_headers()

def _start_mjpeg_server():
    server = ThreadingHTTPServer(('0.0.0.0', STREAM_PORT), _MJPEGHandler)
    log.info(f'MJPEG 스트림: http://localhost:{STREAM_PORT}/stream.mjpeg')
    server.serve_forever()


# ─── RTSP 카메라 리더 ───────────────────────────────────────────────
class _CameraReader:
    """RTSP 스트림을 별도 스레드에서 읽어 최신 프레임을 유지합니다."""

    def __init__(self, url: str, name: str):
        self.url  = url
        self.name = name
        self._frame: np.ndarray | None = None
        self._lock    = threading.Lock()
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True,
                                         name=f'cam-{name}')
        self._thread.start()

    def _loop(self):
        while self._running:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                log.warning(f'[{self.name}] RTSP 연결 실패, 5초 후 재시도…')
                time.sleep(5)
                continue
            log.info(f'[{self.name}] RTSP 연결')
            while self._running:
                ret, frame = cap.read()
                if not ret:
                    log.warning(f'[{self.name}] 프레임 끊김, 재연결')
                    break
                with self._lock:
                    self._frame = frame
            cap.release()

    def get_frame(self) -> np.ndarray | None:
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def stop(self):
        self._running = False


# ─── 메인 루프 ─────────────────────────────────────────
def main():
    global _jpeg_frame, _jpeg_frame_1, _jpeg_frame_2
    log.info('카메라 스트림 서버 시작')
    log.info(f'  카메라 1: {CAMERA_1_URL}')
    log.info(f'  카메라 2: {CAMERA_2_URL}')

    # MJPEG 서버 (백그라운드 스레드)
    t = threading.Thread(target=_start_mjpeg_server, daemon=True)
    t.start()

    cam1 = _CameraReader(CAMERA_1_URL, 'CAM1')
    cam2 = _CameraReader(CAMERA_2_URL, 'CAM2')
    log.info('RTSP 스트림 연결 중…')

    try:
        while True:
            frames = [cam1.get_frame(), cam2.get_frame()]
            indiv_bufs = []

            for frame in frames:
                if frame is None:
                    img = np.zeros((360, 640, 3), dtype=np.uint8)
                else:
                    img = cv2.resize(frame, (640, 360))
                _, ibuf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 70])
                indiv_bufs.append(ibuf.tobytes())

            # 두 프레임 가로 합성
            f1 = cv2.imdecode(np.frombuffer(indiv_bufs[0], np.uint8), cv2.IMREAD_COLOR)
            f2 = cv2.imdecode(np.frombuffer(indiv_bufs[1], np.uint8), cv2.IMREAD_COLOR)
            combined = np.hstack([f1, f2])
            _, buf = cv2.imencode('.jpg', combined, [cv2.IMWRITE_JPEG_QUALITY, 70])

            with _frame_lock:
                _jpeg_frame   = buf.tobytes()
                _jpeg_frame_1 = indiv_bufs[0]
                _jpeg_frame_2 = indiv_bufs[1]

            time.sleep(0.1)   # ~10 fps

    except KeyboardInterrupt:
        log.info('종료 신호 수신')
    finally:
        cam1.stop()
        cam2.stop()
        log.info('카메라 스트림 서버 종료')

if __name__ == '__main__':
    main()
