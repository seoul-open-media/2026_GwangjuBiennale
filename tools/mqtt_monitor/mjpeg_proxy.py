#!/usr/bin/env python3
"""경량 MJPEG 프록시 — RTSP 카메라 → HTTP MJPEG 스트림 (AI 감지 없음)
포트: AUDIENCE_STREAM_PORT (기본 8181)
"""
import os, sys, time, threading, logging
import urllib.parse
import cv2
import numpy as np
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── 환경변수 로드 (.env) ─────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
_env_file = SCRIPT_DIR / '.env'
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _, _v = _line.partition('=')
            os.environ.setdefault(_k.strip(), _v.strip())

_cam_user = os.getenv('TAPO_CAM_USER', 'admin')
_cam_pass = urllib.parse.quote(os.getenv('TAPO_CAM_PASS', ''), safe='')
_cam1_ip  = os.getenv('AUDIENCE_CAMERA_1_IP', '')
_cam2_ip  = os.getenv('AUDIENCE_CAMERA_2_IP', '')
CAMERA_1_URL = f'rtsp://{_cam_user}:{_cam_pass}@{_cam1_ip}/stream2'
CAMERA_2_URL = f'rtsp://{_cam_user}:{_cam_pass}@{_cam2_ip}/stream2'
STREAM_PORT  = int(os.getenv('AUDIENCE_STREAM_PORT', '8181'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [MJPEG] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── 공유 프레임 버퍼 ─────────────────────────────────────────────────
_frame_lock   = threading.Lock()
_jpeg_frame_1 = b''
_jpeg_frame_2 = b''
_jpeg_frame   = b''  # 좌우 합성


class _CameraReader:
    def __init__(self, url: str, name: str):
        self.url  = url
        self.name = name
        self._frame: np.ndarray | None = None
        self._lock    = threading.Lock()
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name=f'cam-{name}').start()

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


class _MJPEGHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args): pass  # access log 억제

    def _common_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('ngrok-skip-browser-warning', 'true')
        self.send_header('Connection', 'close')

    def do_GET(self):
        path = self.path.split('?')[0]

        if path in ('/stream.mjpeg', '/stream1.mjpeg', '/stream2.mjpeg'):
            buf_map = {
                '/stream.mjpeg':  lambda: _jpeg_frame,
                '/stream1.mjpeg': lambda: _jpeg_frame_1,
                '/stream2.mjpeg': lambda: _jpeg_frame_2,
            }
            get_buf = buf_map[path]
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self._common_headers()
            self.end_headers()
            try:
                while True:
                    with _frame_lock:
                        data = get_buf()
                    if data:
                        self.wfile.write(
                            b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + data + b'\r\n'
                        )
                        self.wfile.flush()
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError):
                pass

        elif path in ('/snapshot.jpeg', '/snapshot1.jpeg', '/snapshot2.jpeg'):
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
                self._common_headers()
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(503)
                self.send_header('Connection', 'close')
                self.end_headers()

        # ── /launch/light_gui (LIGHT DISABLED) ──────────────────────────
        # elif path == '/launch/light_gui':
        #     import subprocess, json as _json
        #     gui_path = SCRIPT_DIR.parent.parent / 'light' / 'light_test_gui.py'
        #     try:
        #         # 이미 실행 중이면 중복 실행 방지
        #         already = subprocess.run(
        #             ['pgrep', '-f', 'light_test_gui.py'],
        #             capture_output=True,
        #         )
        #         if already.returncode != 0:
        #             subprocess.Popen(
        #                 ['python3', str(gui_path)],
        #                 start_new_session=True,
        #                 stdout=subprocess.DEVNULL,
        #                 stderr=subprocess.DEVNULL,
        #             )
        #         result = {'ok': True}
        #     except Exception as e:
        #         result = {'ok': False, 'error': str(e)}
        #     body = _json.dumps(result).encode('utf-8')
        #     self.send_response(200)
        #     self.send_header('Content-Type', 'application/json; charset=utf-8')
        #     self.send_header('Content-Length', str(len(body)))
        #     self._common_headers()
        #     self.end_headers()
        #     self.wfile.write(body)

        elif path.startswith('/api/'):
            # bridge.py(8080)로 프록시
            import urllib.request as _ureq, urllib.error as _uerr
            target = 'http://127.0.0.1:8080' + self.path  # query string 포함
            try:
                with _ureq.urlopen(target, timeout=15) as resp:
                    body = resp.read()
                    self.send_response(resp.status)
                    self.send_header('Content-Type', resp.headers.get('Content-Type', 'application/json'))
                    self.send_header('Content-Length', str(len(body)))
                    self._common_headers()
                    self.end_headers()
                    self.wfile.write(body)
            except _uerr.HTTPError as e:
                body = f'{{"error":"upstream HTTP {e.code}"}}'.encode()
                self.send_response(e.code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self._common_headers()
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                body = f'{{"error":"{e}"}}'.encode()
                self.send_response(502)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self._common_headers()
                self.end_headers()
                self.wfile.write(body)

        else:
            # 정적 파일 서빙 (sema_control.html, mqtt.min.js 등)
            safe_name = Path(path.lstrip('/')).name  # 경로 탐색 방지
            if not safe_name:
                safe_name = 'sema_control.html'
            file_path = SCRIPT_DIR / safe_name
            if file_path.exists() and file_path.is_file():
                ext = file_path.suffix.lower()
                mime = {
                    '.html': 'text/html; charset=utf-8',
                    '.js':   'application/javascript; charset=utf-8',
                    '.css':  'text/css; charset=utf-8',
                    '.json': 'application/json; charset=utf-8',
                    '.ico':  'image/x-icon',
                }.get(ext, 'application/octet-stream')
                data = file_path.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('ngrok-skip-browser-warning', 'true')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.send_header('Connection', 'close')
                self.end_headers()


def _encode_loop(cam1: _CameraReader, cam2: _CameraReader):
    """카메라 프레임을 JPEG로 인코딩해 공유 버퍼 갱신."""
    global _jpeg_frame_1, _jpeg_frame_2, _jpeg_frame
    while True:
        f1 = cam1.get_frame()
        f2 = cam2.get_frame()
        buf1 = b''
        buf2 = b''
        if f1 is not None:
            ok, enc = cv2.imencode('.jpg', f1, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                buf1 = enc.tobytes()
        if f2 is not None:
            ok, enc = cv2.imencode('.jpg', f2, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                buf2 = enc.tobytes()

        # 합성: 동일 크기로 리사이즈 후 좌우 연결
        combined = b''
        if f1 is not None and f2 is not None:
            h = min(f1.shape[0], f2.shape[0])
            w = min(f1.shape[1], f2.shape[1])
            r1 = cv2.resize(f1, (w, h))
            r2 = cv2.resize(f2, (w, h))
            ok, enc = cv2.imencode('.jpg', np.hstack([r1, r2]),
                                   [cv2.IMWRITE_JPEG_QUALITY, 65])
            if ok:
                combined = enc.tobytes()
        elif buf1:
            combined = buf1
        elif buf2:
            combined = buf2

        with _frame_lock:
            _jpeg_frame_1 = buf1
            _jpeg_frame_2 = buf2
            _jpeg_frame   = combined

        time.sleep(0.1)


def main():
    log.info(f'경량 MJPEG 프록시 시작 — 포트 {STREAM_PORT}')
    log.info(f'  CAM1: {CAMERA_1_URL}')
    log.info(f'  CAM2: {CAMERA_2_URL}')

    cam1 = _CameraReader(CAMERA_1_URL, 'CAM1')
    cam2 = _CameraReader(CAMERA_2_URL, 'CAM2')

    threading.Thread(target=_encode_loop, args=(cam1, cam2), daemon=True,
                     name='encode').start()

    server = ThreadingHTTPServer(('0.0.0.0', STREAM_PORT), _MJPEGHandler)
    log.info(f'MJPEG http://localhost:{STREAM_PORT}/stream1.mjpeg 준비')
    server.serve_forever()


if __name__ == '__main__':
    main()
