# GB16 DHCP Reservation Plan (Router Reset Recovery)

Use this table in your router's DHCP reservation menu.

## Required reservations (minimum)

| Priority | Device | MAC Address | Reserved IP | Why required |
|---|---|---|---|---|
| 1 | GB16 Host PC (enp4s0) | 34:5a:60:89:e5:57 | 192.168.0.18 | Control page, local MQTT WS URL, host services |
| 2 | Tapo C200C CAM1 | 60:15:6F:0E:4F:FE | 192.168.0.200 | Audience detection RTSP source |
| 3 | Tapo C200C CAM2 (optional) | (fill if used) | 192.168.0.201 | Optional second camera stream/detection |
| 4 | GB16 PD PC | E8:9C:25:A4:A5:CA | 192.168.0.12 | Audience present signal target (UDP 1/0) |

## Optional but recommended

| Device | MAC Address | Reserved IP | Notes |
|---|---|---|---|
| Router / Gateway | 58:86:94:59:13:b7 | 192.168.0.1 | Usually already fixed by router |
| Tapo Smart Plug Group 1 (P110M-1) | C0:3A:55:3F:48:D4 | 192.168.0.210 | If using Tapo power reset automation |
| Tapo Smart Plug Group 2 | (fill) | 192.168.0.211 | If using Tapo power reset automation |
| Tapo Smart Plug Group 3 | (fill) | 192.168.0.212 | If using Tapo power reset automation |
| Tapo Smart Plug Group 4 | (fill) | 192.168.0.213 | If using Tapo power reset automation |

## Match with project config

Current config references these values:
- GB16 host IP: 192.168.0.18
- CAM1 IP: 192.168.0.200
- CAM2 IP: (empty / optional)
- GB16 PD UDP target: 192.168.0.12:7001

If you change reserved IPs, update:
- gb16_host/.env: GB16_HOST_IP, MQTT_BROKER_WS, AUDIENCE_CAMERA_1_IP, AUDIENCE_CAMERA_2_IP

## How to find missing MAC addresses quickly

1. Power on each camera and connect to LAN.
2. In router client list, find device names starting with Tapo/C200.
3. Copy each MAC into this table.
4. Create DHCP reservation entries.
5. Reboot router and verify device IPs.

Alternative from host terminal (after camera is reachable):
- ping -c 1 192.168.0.200
- ip neigh show 192.168.0.200

The `lladdr` value is the MAC address.
