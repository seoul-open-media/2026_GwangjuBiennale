#!/usr/bin/env python3
"""
Tapo P110M 스마트플러그 전원 리셋 유틸리티

설치: pip install tapo
실행: python tapo_reset.py <group_num> [--off-sec N]
       python tapo_reset.py 1          ← 황금칩 꽃 리셋
       python tapo_reset.py 2 3        ← 여러 그룹 동시 리셋
       python tapo_reset.py all        ← 전체 리셋

.env 설정:
  TAPO_EMAIL=your@email.com
  TAPO_PASSWORD=yourpassword
  TAPO_RESET_SEC=5
  TAPO_GROUP_1=192.168.1.101   # 황금칩 꽃
  TAPO_GROUP_2=192.168.1.102   # 황금빛 꽃잎 1
  ...
"""

import asyncio
import sys
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

log = logging.getLogger(__name__)

TAPO_EMAIL    = os.getenv('TAPO_EMAIL',     '')
TAPO_PASSWORD = os.getenv('TAPO_PASSWORD',  '')
OFF_SECONDS   = int(os.getenv('TAPO_RESET_SEC', '30'))

GROUP_NAMES: dict[int, str] = {
    1: '황금칩 꽃',
    2: '황금빛 꽃잎 1',
    3: '황금빛 꽃잎 2',
    4: '비결정적 유영',
    5: '아해들',
}

# 그룹 번호 → 플러그 IP  (.env: TAPO_GROUP_1=192.168.x.x)
PLUG_MAP: dict[int, str] = {}
for _k, _v in os.environ.items():
    if _k.startswith('TAPO_GROUP_'):
        try:
            PLUG_MAP[int(_k[len('TAPO_GROUP_'):])] = _v.strip()
        except ValueError:
            pass


# ── 비동기 리셋 ────────────────────────────────────────────────────────
async def _connect(client, ip: str):
    """p110 연결 시도, Serde 오류 시 p100으로 fallback"""
    try:
        return await client.p110(ip)
    except Exception as e:
        if 'Serde' in str(e) or 'missing field' in str(e):
            log.warning(f"[TAPO] {ip} p110 역직렬화 오류 → p100으로 재시도")
            return await client.p100(ip)
        raise

async def reset_group(group_id: int, off_sec: int = OFF_SECONDS) -> bool:
    """그룹 플러그를 off_sec 초간 꺼다 켠다."""
    try:
        from tapo import ApiClient
    except ImportError:
        log.error("[TAPO] 'tapo' 패키지 미설치: pip install tapo")
        return False

    name = GROUP_NAMES.get(group_id, f'그룹{group_id}')
    ip   = PLUG_MAP.get(group_id)
    if not ip:
        log.warning(f"[TAPO] {name}: TAPO_GROUP_{group_id} 미설정 (.env)")
        return False

    if not TAPO_EMAIL or not TAPO_PASSWORD:
        log.error("[TAPO] TAPO_EMAIL / TAPO_PASSWORD 미설정 (.env)")
        return False

    try:
        client = ApiClient(TAPO_EMAIL, TAPO_PASSWORD)
        device = await _connect(client, ip)
        log.info(f"[TAPO] {name} ({ip}) → OFF")
        await device.off()
        await asyncio.sleep(off_sec)
        await device.on()
        log.info(f"[TAPO] {name} ({ip}) → ON  ({off_sec}s 후)")
        return True
    except Exception as e:
        log.error(f"[TAPO] {name} ({ip}) 오류: {e}")
        return False


# ── 비동기 단순 ON / OFF ──────────────────────────────────────────────
async def set_group_power(group_id: int, on: bool) -> bool:
    """그룹 플러그를 단순히 켜거나 끈다."""
    try:
        from tapo import ApiClient
    except ImportError:
        log.error("[TAPO] 'tapo' 패키지 미설치: pip install tapo")
        return False

    name = GROUP_NAMES.get(group_id, f'그룹{group_id}')
    ip   = PLUG_MAP.get(group_id)
    if not ip:
        log.warning(f"[TAPO] {name}: TAPO_GROUP_{group_id} 미설정 (.env)")
        return False

    if not TAPO_EMAIL or not TAPO_PASSWORD:
        log.error("[TAPO] TAPO_EMAIL / TAPO_PASSWORD 미설정 (.env)")
        return False

    try:
        client = ApiClient(TAPO_EMAIL, TAPO_PASSWORD)
        device = await _connect(client, ip)
        if on:
            await device.on()
            log.info(f"[TAPO] {name} ({ip}) → ON")
        else:
            await device.off()
            log.info(f"[TAPO] {name} ({ip}) → OFF")
        return True
    except Exception as e:
        log.error(f"[TAPO] {name} ({ip}) 오류: {e}")
        return False


# ── 동기 래퍼 (bridge.py 스레드에서 호출용) ────────────────────────────
def reset_group_sync(group_id: int, off_sec: int = OFF_SECONDS) -> bool:
    return asyncio.run(reset_group(group_id, off_sec))

def set_group_power_sync(group_id: int, on: bool) -> bool:
    return asyncio.run(set_group_power(group_id, on))


# ── CLI ───────────────────────────────────────────────────────────────
async def _main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(message)s',
                        datefmt='%H:%M:%S')
    args    = sys.argv[1:]
    off_sec = OFF_SECONDS

    if '--off-sec' in args:
        idx     = args.index('--off-sec')
        off_sec = int(args[idx + 1])
        args    = [a for i, a in enumerate(args) if i not in (idx, idx + 1)]

    if not args:
        print("사용법: python tapo_reset.py <group_num|all> [--off-sec N]")
        print("그룹 목록:")
        for gid, name in GROUP_NAMES.items():
            ip = PLUG_MAP.get(gid, '미설정')
            print(f"  {gid}. {name:12s}  {ip}")
        return

    targets = list(PLUG_MAP.keys()) if args[0] == 'all' else [int(x) for x in args]
    results = await asyncio.gather(*[reset_group(gid, off_sec) for gid in targets])
    ok = sum(results)
    print(f"[TAPO] {ok}/{len(targets)} 완료")


if __name__ == '__main__':
    asyncio.run(_main())
