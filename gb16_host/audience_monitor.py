#!/usr/bin/env python3
"""Audience monitor for Tapo RTSP cameras.

Features:
- RTSP ingest from up to 2 cameras
- YOLO person detection (Ultralytics)
- MQTT publish: audience/present, audience/cam1/present, audience/cam2/present
- MJPEG server: /stream1.mjpeg, /stream2.mjpeg, /stream.mjpeg

Run:
  python3 audience_monitor.py
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from ultralytics import YOLO

SCRIPT_DIR = Path(__file__).parent
load_dotenv(SCRIPT_DIR / ".env")


@dataclass
class Config:
    mqtt_broker: str = os.getenv("MQTT_BROKER", "127.0.0.1")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_user: str = os.getenv("MQTT_USER", "")
    mqtt_pass: str = os.getenv("MQTT_PASS", "")
    mqtt_tls: bool = os.getenv("MQTT_TLS", "").strip() in {"1", "true", "True"}

    cam_user: str = os.getenv("TAPO_CAM_USER", "admin")
    cam_pass: str = os.getenv("TAPO_CAM_PASS", "")
    cam1_ip: str = os.getenv("AUDIENCE_CAMERA_1_IP", "").strip()
    cam2_ip: str = os.getenv("AUDIENCE_CAMERA_2_IP", "").strip()
    cam_rtsp_path: str = os.getenv("AUDIENCE_CAMERA_RTSP_PATH", "/stream2").strip()

    stream_port: int = int(os.getenv("AUDIENCE_STREAM_PORT", "8181"))
    detect_interval_s: float = float(os.getenv("AUDIENCE_DETECT_INTERVAL", "0.4"))
    detect_conf: float = float(os.getenv("AUDIENCE_DETECT_CONF", "0.35"))
    detect_present_hits: int = int(os.getenv("AUDIENCE_PRESENT_HITS", "2"))
    detect_absent_hold_s: float = float(os.getenv("AUDIENCE_ABSENT_HOLD_SEC", "4.0"))
    yolo_model: str = os.getenv(
        "AUDIENCE_YOLO_MODEL",
        str((SCRIPT_DIR.parent / "tools" / "mqtt_monitor" / "yolov8n.pt").resolve()),
    )


CFG = Config()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AUDIENCE] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("audience_monitor")


class CameraReader:
    def __init__(self, name: str, urls: list[str]):
        self.name = name
        self.urls = urls
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name=f"cam-{name}").start()

    def _loop(self):
        idx = 0
        consecutive_fail = 0
        # Camera devices (Tapo/TP-Link) apply anti-brute-force lockout after
        # repeated failed RTSP logins. Back off exponentially instead of
        # hammering the camera every few seconds.
        while self._running:
            url = self.urls[idx % len(self.urls)]
            idx += 1
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                consecutive_fail += 1
                delay = min(5 * (2 ** min(consecutive_fail - 1, 5)), 300)  # 5s..300s cap
                log.warning(
                    "[%s] RTSP connect failed (%s). fail_count=%d, retry in %ds",
                    self.name, url, consecutive_fail, delay,
                )
                if consecutive_fail == 5:
                    log.error(
                        "[%s] 5 consecutive RTSP auth/connect failures. "
                        "Camera may enforce an anti-brute-force lockout — "
                        "verify credentials in the Tapo app and avoid rapid retries.",
                        self.name,
                    )
                cap.release()
                time.sleep(delay)
                continue

            consecutive_fail = 0
            log.info("[%s] RTSP connected (%s)", self.name, url)
            while self._running:
                ok, frame = cap.read()
                if not ok:
                    log.warning("[%s] frame dropped. reconnect", self.name)
                    break
                with self._lock:
                    self._frame = frame
            cap.release()

    def get_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy()


def _split_users(raw: str) -> list[str]:
    # Allow admin/byungjun or admin,byungjun style inputs.
    toks = raw.replace('|', '/').replace(',', '/').split('/')
    out = [t.strip() for t in toks if t.strip()]
    return out or ['admin']


class PresenceGate:
    def __init__(self, present_hits: int, absent_hold_s: float):
        self.present_hits = max(1, present_hits)
        self.absent_hold_s = max(0.0, absent_hold_s)
        self.present = False
        self._hit_streak = 0
        self._last_person_ts = 0.0

    def update(self, person_found: bool, now: float) -> bool:
        changed = False
        if person_found:
            self._hit_streak += 1
            self._last_person_ts = now
            if not self.present and self._hit_streak >= self.present_hits:
                self.present = True
                changed = True
        else:
            self._hit_streak = 0
            if self.present and (now - self._last_person_ts) >= self.absent_hold_s:
                self.present = False
                changed = True
        return changed


class AudienceMonitor:
    def __init__(self, cfg: Config):
        self.cfg = cfg

        cam_pass_q = urllib.parse.quote(cfg.cam_pass, safe="")
        users = _split_users(cfg.cam_user)
        if not cfg.cam1_ip:
            raise RuntimeError("AUDIENCE_CAMERA_1_IP is required in .env")

        cam1_urls = [f"rtsp://{u}:{cam_pass_q}@{cfg.cam1_ip}{cfg.cam_rtsp_path}" for u in users]
        self.cam1 = CameraReader("CAM1", cam1_urls)

        self.cam2 = None
        if cfg.cam2_ip:
            cam2_urls = [f"rtsp://{u}:{cam_pass_q}@{cfg.cam2_ip}{cfg.cam_rtsp_path}" for u in users]
            self.cam2 = CameraReader("CAM2", cam2_urls)

        model_path = Path(cfg.yolo_model)
        if not model_path.exists():
            raise RuntimeError(f"YOLO model not found: {model_path}")
        self.model = YOLO(str(model_path))
        try:
            import torch
            self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        except Exception:
            self.device = "cpu"
        log.info("YOLO inference device: %s", self.device)

        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="gb16_audience")
        if cfg.mqtt_user:
            self.mqtt.username_pw_set(cfg.mqtt_user, cfg.mqtt_pass)
        if cfg.mqtt_tls:
            self.mqtt.tls_set()

        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_disconnect = self._on_disconnect

        self.gate1 = PresenceGate(cfg.detect_present_hits, cfg.detect_absent_hold_s)
        self.gate2 = PresenceGate(cfg.detect_present_hits, cfg.detect_absent_hold_s)
        self.gate_all = PresenceGate(1, cfg.detect_absent_hold_s)

        self.last_detect_ts_1 = 0.0
        self.last_detect_ts_2 = 0.0

        self.frame_lock = threading.Lock()
        self.jpeg1 = b""
        self.jpeg2 = b""
        self.jpeg_combined = b""

        self.last_count_1 = 0
        self.last_count_2 = 0

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        log.info("MQTT connected rc=%s", reason_code)
        self._publish_presence("audience/cam1/present", False, force=True)
        self._publish_presence("audience/cam2/present", False, force=True)
        self._publish_presence("audience/present", False, force=True)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        log.warning("MQTT disconnected rc=%s", reason_code)

    def _publish_presence(self, topic: str, present: bool, force: bool = False):
        payload = "true" if present else "false"
        info = self.mqtt.publish(topic, payload, qos=0, retain=True)
        if force:
            info.wait_for_publish(timeout=2)

    def _detect_people(self, frame: np.ndarray) -> tuple[np.ndarray, int, bool]:
        result = self.model.predict(
            source=frame,
            conf=self.cfg.detect_conf,
            classes=[0],
            verbose=False,
            imgsz=640,
            device=self.device,
        )[0]

        person_count = 0
        annotated = frame.copy()
        if result.boxes is not None:
            for box in result.boxes:
                cls = int(box.cls[0]) if box.cls is not None else -1
                if cls != 0:
                    continue
                person_count += 1
                x1, y1, x2, y2 = box.xyxy[0].int().tolist()
                conf = float(box.conf[0]) if box.conf is not None else 0.0
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (80, 220, 120), 2)
                cv2.putText(
                    annotated,
                    f"person {conf:.2f}",
                    (x1, max(18, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (80, 220, 120),
                    1,
                    cv2.LINE_AA,
                )

        return annotated, person_count, person_count > 0

    def _process_camera(
        self,
        frame: Optional[np.ndarray],
        camera_idx: int,
        now: float,
    ) -> tuple[np.ndarray, int, bool]:
        if frame is None:
            canvas = np.zeros((360, 640, 3), dtype=np.uint8)
            cv2.putText(canvas, f"CAM{camera_idx} no signal", (20, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (90, 90, 255), 2)
            return canvas, 0, False

        last_detect_ts = self.last_detect_ts_1 if camera_idx == 1 else self.last_detect_ts_2
        if now - last_detect_ts >= self.cfg.detect_interval_s:
            annotated, count, person_found = self._detect_people(frame)
            if camera_idx == 1:
                self.last_detect_ts_1 = now
                self.last_count_1 = count
                changed = self.gate1.update(person_found, now)
                if changed:
                    self._publish_presence("audience/cam1/present", self.gate1.present)
                    log.info("CAM1 present=%s count=%d", self.gate1.present, count)
            else:
                self.last_detect_ts_2 = now
                self.last_count_2 = count
                changed = self.gate2.update(person_found, now)
                if changed:
                    self._publish_presence("audience/cam2/present", self.gate2.present)
                    log.info("CAM2 present=%s count=%d", self.gate2.present, count)
            return annotated, count, person_found

        count = self.last_count_1 if camera_idx == 1 else self.last_count_2
        return frame, count, count > 0

    def _encode_jpeg(self, frame: np.ndarray, quality: int = 75) -> bytes:
        ok, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            return b""
        return enc.tobytes()

    def _overlay_header(self, frame: np.ndarray, label: str, count: int, present: bool) -> np.ndarray:
        out = frame.copy()
        status = "PRESENT" if present else "ABSENT"
        color = (80, 220, 120) if present else (120, 120, 255)
        cv2.rectangle(out, (0, 0), (out.shape[1], 30), (12, 12, 12), -1)
        cv2.putText(out, f"{label} | persons={count} | {status}", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
        return out

    def encode_loop(self):
        while True:
            now = time.time()
            frame1 = self.cam1.get_frame()
            frame2 = self.cam2.get_frame() if self.cam2 is not None else None

            p1, c1, _ = self._process_camera(frame1, 1, now)
            p2, c2, _ = self._process_camera(frame2, 2, now)

            p1 = self._overlay_header(p1, "CAM1", c1, self.gate1.present)
            if self.cam2 is None:
                p2 = np.zeros_like(p1)
                cv2.putText(p2, "CAM2 not configured", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (180, 180, 180), 2)
            p2 = self._overlay_header(p2, "CAM2", c2, self.gate2.present)

            overall_changed = self.gate_all.update(self.gate1.present or self.gate2.present, now)
            if overall_changed:
                self._publish_presence("audience/present", self.gate_all.present)
                log.info("ALL present=%s", self.gate_all.present)

            h = min(p1.shape[0], p2.shape[0])
            w = min(p1.shape[1], p2.shape[1])
            c1f = cv2.resize(p1, (w, h))
            c2f = cv2.resize(p2, (w, h))
            combined = np.hstack([c1f, c2f])

            b1 = self._encode_jpeg(c1f, quality=78)
            b2 = self._encode_jpeg(c2f, quality=78)
            bc = self._encode_jpeg(combined, quality=72)

            with self.frame_lock:
                self.jpeg1 = b1
                self.jpeg2 = b2
                self.jpeg_combined = bc

            time.sleep(0.06)

    def run(self):
        log.info("CAM1 IP: %s", self.cfg.cam1_ip)
        log.info("CAM2 IP: %s", self.cfg.cam2_ip if self.cfg.cam2_ip else "(not configured)")
        log.info("MJPEG stream port: %d", self.cfg.stream_port)

        self.mqtt.connect(self.cfg.mqtt_broker, self.cfg.mqtt_port, keepalive=30)
        self.mqtt.loop_start()

        threading.Thread(target=self.encode_loop, daemon=True, name="audience-encode").start()

        monitor = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                return

            def _common_headers(self):
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")

            def _stream(self, getter):
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self._common_headers()
                self.end_headers()
                try:
                    while True:
                        with monitor.frame_lock:
                            data = getter()
                        if data:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n")
                            self.wfile.flush()
                        time.sleep(0.08)
                except (BrokenPipeError, ConnectionResetError):
                    return

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/stream1.mjpeg":
                    self._stream(lambda: monitor.jpeg1)
                    return
                if path == "/stream2.mjpeg":
                    self._stream(lambda: monitor.jpeg2)
                    return
                if path == "/stream.mjpeg":
                    self._stream(lambda: monitor.jpeg_combined)
                    return

                if path == "/snapshot1.jpeg":
                    with monitor.frame_lock:
                        data = monitor.jpeg1
                elif path == "/snapshot2.jpeg":
                    with monitor.frame_lock:
                        data = monitor.jpeg2
                elif path == "/snapshot.jpeg":
                    with monitor.frame_lock:
                        data = monitor.jpeg_combined
                else:
                    data = None

                if data:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self._common_headers()
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_response(503)
                    self._common_headers()
                    self.end_headers()

        server = ThreadingHTTPServer(("0.0.0.0", self.cfg.stream_port), Handler)
        log.info("MJPEG endpoints: /stream1.mjpeg /stream2.mjpeg /stream.mjpeg")
        server.serve_forever()


def main():
    try:
        mon = AudienceMonitor(CFG)
        mon.run()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        log.error("audience monitor fatal: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
