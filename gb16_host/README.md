# GB16 Host — GB16 2026 광주비엔날레 호스트 프레임워크

sema_host / bridge.py 기반으로 작성된 광주비엔날레 전용 호스트입니다.
sema 전시와 포트가 분리되어 있어 동일 머신에서 동시 운용 가능합니다.

## 파일 구조

```
gb16_host/
  gb16_host.py          시리얼 게이트웨이 (XBee ↔ UDP)
  gb16_bridge.py        MQTT 브리지 (UDP ↔ MQTT + 통계 API)
  start.sh              시작 스크립트
  stop.sh               종료 스크립트
  .env.example          환경 변수 예시
  systemd/
    gb16-host.service   systemd 유닛 (gb16_host.py)
    gb16-bridge.service systemd 유닛 (gb16_bridge.py)
  logs/                 로그 파일 (자동 생성)
```

## sema와 달라진 점

| 항목             | sema        | GB16        |
|-----------------|-------------|-------------|
| STATUS UDP 포트  | 5005        | 6005        |
| CMD UDP 포트     | 5006        | 6006        |
| MQTT 토픽 접두사  | `sma/`      | `gb16/`     |
| 통계 API 포트    | 8080        | 8182        |
| DB 파일          | sma_data.db | gb16_data.db|
| systemd 서비스   | sema-host   | gb16-host   |

## 설치 및 실행

### 1. 의존성 설치
```bash
pip install pyserial paho-mqtt python-dotenv
```

관객 감지(카메라+YOLO)까지 사용하려면 추가 설치:
```bash
pip install -r requirements-audience.txt
```

### 2. 환경 변수 설정
```bash
cp .env.example .env
# .env 파일 수정 (NGROK_DOMAIN, MQTT_BROKER 등)
```

카메라 설정 필수 항목:
```env
TAPO_CAM_USER=admin
TAPO_CAM_PASS=YOUR_TAPO_CAMERA_PASSWORD
AUDIENCE_CAMERA_1_IP=192.168.0.200
AUDIENCE_STREAM_PORT=8181
```

카메라 고정 IP 권장 설정:
- 공유기 DHCP Reservation(권장): 카메라 MAC 주소를 고정 IP에 매핑
- 예: C200C MAC -> 192.168.0.200
- 앱에서 IP를 수동 지정하는 방식보다 공유기 예약이 더 안정적

### 3. systemd 서비스 등록 (최초 1회)
```bash
mkdir -p ~/.config/systemd/user
cp systemd/gb16-host.service   ~/.config/systemd/user/
cp systemd/gb16-bridge.service ~/.config/systemd/user/
cp systemd/gb16-audience.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable gb16-host.service gb16-bridge.service gb16-audience.service
```

### 4. 실행 / 종료
```bash
./start.sh   # 시작
./stop.sh    # 종료
```

### 5. 상태 확인
```bash
systemctl --user status gb16-host.service
systemctl --user status gb16-bridge.service
systemctl --user status gb16-audience.service
journalctl --user -u gb16-host.service -f
```

## 관객 감지/카메라 스트림

- 실행 파일: `audience_monitor.py`
- 감지 토픽:
  - `audience/cam1/present`
  - `audience/cam2/present`
  - `audience/present` (cam1 or cam2)
- 스트림 URL:
  - `http://<host>:8181/stream1.mjpeg`
  - `http://<host>:8181/stream2.mjpeg`
  - `http://<host>:8181/stream.mjpeg` (좌우 합성)

`gb16_control.html`은 기본적으로 위 스트림을 사용해 카메라 화면을 표시합니다.

## 대시보드 HTML

`gb16_bridge.py`는 `STATIC_DIR` (기본값: `gb16_host/`)에서 정적 파일을 서빙합니다.

sema의 HTML 파일을 재사용하려면 `.env`에 설정:
```
STATIC_DIR=/home/byungjun/Documents/GitHub/2026_GwangjuBiennale/tools/mqtt_monitor
```

또는 `tools/mqtt_monitor/`의 HTML 파일을 `gb16_host/`에 복사:
```bash
cp ../tools/mqtt_monitor/dashboard.html .
cp ../tools/mqtt_monitor/sema_control.html gb16_control.html
# gb16_control.html 내 'sma/' → 'gb16/' 치환 필요
sed -i "s/sma\//gb16\//g" gb16_control.html
```

## MQTT 토픽

```
gb16/robot/<id>/log     로봇 상태 (retain)
gb16/robot/<id>/cmd     로봇 명령
gb16/robot/<id>/ack     명령 응답
gb16/power/group/<id>   전원 제어
gb16/cue                PD 큐 트리거
audience/present        관람객 감지
```
