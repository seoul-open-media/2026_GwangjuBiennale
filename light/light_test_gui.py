#!/usr/bin/env python3
"""
SeMA 2026 North — 조명 테스트 GUI
light/light_test_gui.py

18개 Gantom RGBW (4ch) 조명 테스트 도구
MQTT → dmx_controller.py 경유 (USB 충돌 없음)

Usage:
  cd /home/nuc/Documents/Github/2026_SeMA_North
  python3 light/light_test_gui.py
"""

import sys
import time
import signal
import threading
import datetime
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("paho-mqtt가 없습니다: pip install paho-mqtt")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════
#  조명 맵  (dmx_controller.py 와 동일)
# ══════════════════════════════════════════════════════════════════
LIGHT_MAP = [
    # 황금빛 꽃  L01~L06  ch 1~24
    {'id':  1, 'ch':  1, 'group': 'gp_flower', 'label': '황금빛 꽃\nL01'},
    {'id':  2, 'ch':  5, 'group': 'gp_flower', 'label': '황금빛 꽃\nL02'},
    {'id':  3, 'ch':  9, 'group': 'gp_flower', 'label': '황금빛 꽃\nL03'},
    {'id':  4, 'ch': 13, 'group': 'gp_flower', 'label': '황금빛 꽃\nL04'},
    {'id':  5, 'ch': 17, 'group': 'gp_flower', 'label': '황금빛 꽃\nL05'},
    {'id':  6, 'ch': 21, 'group': 'gp_flower', 'label': '황금빛 꽃\nL06'},
    # 아해들  L07~L12  ch 25~48
    {'id':  7, 'ch': 25, 'group': 'children',  'label': '아해들\nL07'},
    {'id':  8, 'ch': 29, 'group': 'children',  'label': '아해들\nL08'},
    {'id':  9, 'ch': 33, 'group': 'children',  'label': '아해들\nL09'},
    {'id': 10, 'ch': 37, 'group': 'children',  'label': '아해들\nL10'},
    {'id': 11, 'ch': 41, 'group': 'children',  'label': '아해들\nL11'},
    {'id': 12, 'ch': 45, 'group': 'children',  'label': '아해들\nL12'},
    # 황금빛 꽃잎 2  L13~L16  ch 49~64  (GP 11~12)
    {'id': 13, 'ch': 49, 'group': 'gp_petal2', 'label': '꽃잎2\nL13'},
    {'id': 14, 'ch': 53, 'group': 'gp_petal2', 'label': '꽃잎2\nL14'},
    {'id': 15, 'ch': 57, 'group': 'gp_petal2', 'label': '꽃잎2\nL15'},
    {'id': 16, 'ch': 61, 'group': 'gp_petal2', 'label': '꽃잎2\nL16'},
    # 황금빛 꽃잎 1  L17~L20  ch 65~80  (GP 9~10)
    {'id': 17, 'ch': 65, 'group': 'gp_petal1', 'label': '꽃잎1\nL17'},
    {'id': 18, 'ch': 69, 'group': 'gp_petal1', 'label': '꽃잎1\nL18'},
    {'id': 19, 'ch': 73, 'group': 'gp_petal1', 'label': '꽃잎1\nL19'},
    {'id': 20, 'ch': 77, 'group': 'gp_petal1', 'label': '꽃잎1\nL20'},
]

GROUPS_ORDER = ['gp_flower', 'children', 'gp_petal2', 'gp_petal1']

GROUP_META = {
    'gp_flower': {'label': '황금빛 꽃',     'cols': 6, 'bg': '#1c1200', 'fg': '#ffcc44'},
    'children':  {'label': '아해들',         'cols': 6, 'bg': '#111111', 'fg': '#dddddd'},
    'gp_petal2': {'label': '황금빛 꽃잎 2', 'cols': 4, 'bg': '#181000', 'fg': '#ffaa22'},
    'gp_petal1': {'label': '황금빛 꽃잎 1', 'cols': 4, 'bg': '#181000', 'fg': '#ffaa22'},
}

# 프리셋 (이름, RGBW)
PRESETS = [
    ('BLACKOUT',    (  0,   0,   0,   0)),
    ('황금빛 만개', (255, 148,   0, 130)),
    ('호박빛 대기', ( 45,  18,   0,  35)),
    ('오렌지 가열', (230,  75,   0,  55)),
    ('청록 유영',   ( 18, 170, 230,  75)),
    ('따뜻한 백색', ( 95,  72,  22, 185)),
    ('순백색',      (255, 255, 255, 255)),
    ('적색 경고',   (255,   0,   0,   0)),
]

# ══════════════════════════════════════════════════════════════════
#  MQTT 설정  (dmx_controller.py 와 통신)
# ══════════════════════════════════════════════════════════════════
_BROKER      = 'localhost'
_BROKER_PORT = 1883
_MQTT_USER   = 'sema_north'
_MQTT_PASS   = 'som123'
_TOPIC       = 'sma/light/cmd'

_mqtt_client  = None
_mqtt_connected = False


def mqtt_publish(cmd: str):
    """sma/light/cmd 토픽에 명령 발행. 미연결 시 조용히 무시."""
    if _mqtt_client and _mqtt_connected:
        _mqtt_client.publish(_TOPIC, cmd, qos=1)


def set_light_dmx(light_id: int, r: int, g: int, b: int, w: int):
    mqtt_publish(f'MODE:MANUAL')
    mqtt_publish(f'SET:{light_id}:{r},{g},{b},{w}')


def set_group_dmx(group: str, r: int, g: int, b: int, w: int):
    mqtt_publish('MODE:MANUAL')
    for lt in LIGHT_MAP:
        if lt['group'] == group:
            mqtt_publish(f'SET:{lt["id"]}:{r},{g},{b},{w}')


def set_all_dmx(r: int, g: int, b: int, w: int):
    mqtt_publish('MODE:MANUAL')
    mqtt_publish(f'SET:all:{r},{g},{b},{w}')



# ══════════════════════════════════════════════════════════════════
#  LightCard — 단일 조명 제어 위젯
# ══════════════════════════════════════════════════════════════════
class LightCard(tk.Frame):
    _CH_COLORS = {'r': '#cc3333', 'g': '#33aa44', 'b': '#3366cc', 'w': '#aaaaaa'}

    def __init__(self, parent, light: dict, bg_color: str, on_change_cb, **kw):
        super().__init__(parent, bg=bg_color, padx=5, pady=4,
                         relief='flat', **kw)
        self._light  = light
        self._bg     = bg_color
        self._cb     = on_change_cb
        self._vars   = {}
        self._build()

    def _build(self):
        bg = self._bg
        lt = self._light

        # ─ 헤더 ─
        hdr = tk.Frame(self, bg=bg)
        hdr.pack(fill='x')

        tk.Label(hdr, text=f"L{lt['id']:02d}", bg=bg, fg='#666',
                 font=('monospace', 8)).pack(side='left')
        tk.Label(hdr, text=f"ch{lt['ch']}", bg=bg, fg='#444',
                 font=('monospace', 7)).pack(side='right')

        tk.Label(self, text=lt['label'], bg=bg, fg='#cccccc',
                 font=('Segoe UI', 8, 'bold'), justify='center').pack()

        # ─ 색상 미리보기 ─
        self._preview = tk.Canvas(self, width=170, height=24,
                                  bg='#000000', highlightthickness=1,
                                  highlightbackground='#333')
        self._preview.pack(pady=(2, 4))

        # ─ RGBW 슬라이더 ─
        for ch in ('r', 'g', 'b', 'w'):
            row  = tk.Frame(self, bg=bg)
            row.pack(fill='x', pady=0)
            fg_c = self._CH_COLORS[ch]
            tk.Label(row, text=ch.upper(), bg=bg, fg=fg_c,
                     font=('monospace', 8, 'bold'), width=2).pack(side='left')
            var = tk.IntVar(value=0)
            tk.Scale(row, from_=0, to=255, orient='horizontal',
                     variable=var, bg=bg, fg='#ccc',
                     troughcolor='#1e1e1e', highlightthickness=0,
                     activebackground=fg_c, length=115, showvalue=False,
                     command=lambda _, v=var, c=ch: self._on_slider()
                     ).pack(side='left')
            tk.Label(row, textvariable=var, bg=bg, fg='#aaa',
                     font=('monospace', 8), width=4).pack(side='left')
            self._vars[ch] = var

    def _on_slider(self):
        r, g, b, w = self._get_rgbw()
        set_light_dmx(self._light['id'], r, g, b, w)
        self._update_preview(r, g, b, w)
        self._cb()

    def _update_preview(self, r: int, g: int, b: int, w: int):
        # W를 모든 채널에 더해 백색 혼합 시뮬레이션
        rr = min(255, r + int(w * 0.85))
        gg = min(255, g + int(w * 0.85))
        bb = min(255, b + int(w * 0.85))
        col = f'#{rr:02x}{gg:02x}{bb:02x}'
        self._preview.config(bg=col)

    def _get_rgbw(self) -> tuple:
        return (self._vars['r'].get(), self._vars['g'].get(),
                self._vars['b'].get(), self._vars['w'].get())

    def set_rgbw(self, r: int, g: int, b: int, w: int):
        """외부에서 값 설정 (슬라이더 + 미리보기 동기화)"""
        self._vars['r'].set(r)
        self._vars['g'].set(g)
        self._vars['b'].set(b)
        self._vars['w'].set(w)
        self._update_preview(r, g, b, w)


# ══════════════════════════════════════════════════════════════════
#  메인 애플리케이션
# ══════════════════════════════════════════════════════════════════
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('SeMA 2026 North — 조명 테스트')
        self.configure(bg='#0f1117')
        self.minsize(960, 680)
        self._cards: dict[int, LightCard] = {}
        self._connected = False
        self._chase_running = False

        self._build_top()
        self._build_preset_bar()
        self._build_scroll_area()
        self._build_status_bar()

        self.after(500, self._tick)
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        # 시작 시 자동 MQTT 연결
        self.after(200, self._toggle_connect)

    # ─────────────────────────────────────────────────────────────
    #  UI 구성
    # ─────────────────────────────────────────────────────────────
    def _build_top(self):
        f = tk.Frame(self, bg='#0a0c12', pady=8)
        f.pack(fill='x', padx=0)

        # 제목
        tk.Label(f, text='DMX 조명 테스트', bg='#0a0c12',
                 fg='#ffcc44', font=('Segoe UI', 13, 'bold'),
                 padx=14).pack(side='left')

        # 연결 상태
        self._conn_lbl = tk.Label(f, text='● MQTT 연결 중...', bg='#0a0c12',
                                   fg='#888', font=('Segoe UI', 10, 'bold'))
        self._conn_lbl.pack(side='left', padx=10)

        # 우측: AUTO 복귀 + 연결 버튼
        right = tk.Frame(f, bg='#0a0c12')
        right.pack(side='right', padx=12)

        tk.Button(right, text='AUTO 복귀', bg='#1e2030', fg='#7eb8f7',
                  activebackground='#2a3050', relief='flat', padx=10,
                  font=('Segoe UI', 9),
                  command=lambda: (mqtt_publish('MODE:AUTO'),
                                   self._status('AUTO 모드로 복귀'))
                  ).pack(side='left', padx=4)

        self._conn_btn = tk.Button(
            right, text='재연결', bg='#1a4a1a', fg='#cfc',
            activebackground='#1e6020', relief='flat',
            padx=12, font=('Segoe UI', 9, 'bold'),
            command=self._toggle_connect)
        self._conn_btn.pack(side='left', padx=6)

    def _build_preset_bar(self):
        f = tk.Frame(self, bg='#131620', pady=6)
        f.pack(fill='x', padx=0)

        # 전체 프리셋
        tk.Label(f, text='전체:', bg='#131620', fg='#777',
                 font=('Segoe UI', 8), padx=10).pack(side='left')

        for name, rgbw in PRESETS:
            bg = '#2a0000' if '경고' in name or 'BLACK' in name else '#1e2030'
            fg = '#ff8888' if '경고' in name else ('#888' if 'BLACK' in name else '#ddd')
            tk.Button(f, text=name, bg=bg, fg=fg,
                      activebackground='#2a3050', relief='flat',
                      padx=7, pady=1, font=('Segoe UI', 8),
                      command=lambda v=rgbw: self._preset_all(v)
                      ).pack(side='left', padx=2)

        # 구분선
        tk.Frame(f, bg='#333', width=1).pack(side='left', fill='y',
                                              padx=8, pady=2)

        # Chase 테스트
        tk.Label(f, text='테스트:', bg='#131620', fg='#777',
                 font=('Segoe UI', 8)).pack(side='left')

        self._chase_btn = tk.Button(
            f, text='▶ Chase', bg='#12203a', fg='#7eb8f7',
            activebackground='#1a2a4a', relief='flat',
            padx=8, font=('Segoe UI', 8),
            command=self._toggle_chase)
        self._chase_btn.pack(side='left', padx=2)

        tk.Button(f, text='전체 OFF', bg='#1a1a1a', fg='#888',
                  activebackground='#222', relief='flat',
                  padx=8, font=('Segoe UI', 8),
                  command=lambda: self._preset_all((0, 0, 0, 0))
                  ).pack(side='right', padx=10)

    def _build_scroll_area(self):
        outer = tk.Frame(self, bg='#0f1117')
        outer.pack(fill='both', expand=True)

        canvas = tk.Canvas(outer, bg='#0f1117', highlightthickness=0)
        vsb    = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)

        vsb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        self._inner = tk.Frame(canvas, bg='#0f1117')
        win_id = canvas.create_window((0, 0), window=self._inner, anchor='nw')

        def _on_canvas_resize(e):
            canvas.itemconfig(win_id, width=e.width)

        canvas.bind('<Configure>', _on_canvas_resize)
        self._inner.bind('<Configure>',
                         lambda e: canvas.configure(
                             scrollregion=canvas.bbox('all')))
        # 마우스 휠 스크롤
        canvas.bind_all('<MouseWheel>',
                        lambda e: canvas.yview_scroll(
                            -1 * (e.delta // 120), 'units'))
        canvas.bind_all('<Button-4>',
                        lambda e: canvas.yview_scroll(-1, 'units'))
        canvas.bind_all('<Button-5>',
                        lambda e: canvas.yview_scroll(1, 'units'))

        self._build_group_cards()

    def _build_group_cards(self):
        for gkey in GROUPS_ORDER:
            meta   = GROUP_META[gkey]
            lights = [lt for lt in LIGHT_MAP if lt['group'] == gkey]
            gbg    = meta['bg']
            cols   = meta['cols']

            # ─ 그룹 헤더 ─
            hdr = tk.Frame(self._inner, bg=gbg, pady=4)
            hdr.pack(fill='x', pady=(10, 0))

            tk.Label(hdr, text=f"  {meta['label']}  ({len(lights)}개)",
                     bg=gbg, fg=meta['fg'],
                     font=('Segoe UI', 10, 'bold'),
                     anchor='w').pack(side='left', fill='x', expand=True)

            # 그룹 OFF 버튼
            tk.Button(hdr, text='OFF', bg='#1a1a1a', fg='#666',
                      activebackground='#222', relief='flat',
                      padx=10, font=('Segoe UI', 8),
                      command=lambda g=gkey: self._group_off(g)
                      ).pack(side='right', padx=8)

            # 그룹 프리셋 (그룹별 대표 색)
            group_preset = {
                'gp_flower': ('황금빛', (255, 148, 0, 130)),
                'gp_petal1': ('황금빛', (255, 148, 0, 130)),
                'gp_petal2': ('황금빛', (255, 148, 0, 130)),
                'if':        ('청록',   (18, 170, 230, 75)),
                'children':  ('백색',   (95, 72, 22, 185)),
            }.get(gkey)
            if group_preset:
                plabel, prgbw = group_preset
                tk.Button(hdr, text=plabel, bg=gbg, fg=meta['fg'],
                          activebackground='#222', relief='flat',
                          padx=10, font=('Segoe UI', 8),
                          command=lambda g=gkey, v=prgbw: self._group_preset(g, v)
                          ).pack(side='right', padx=2)

            # ─ 카드 그리드 ─
            grid = tk.Frame(self._inner, bg='#0f1117')
            grid.pack(fill='x', padx=4, pady=4)

            for i, lt in enumerate(lights):
                row_idx = i // cols
                col_idx = i % cols
                card = LightCard(
                    grid, lt, gbg,
                    on_change_cb=self._on_card_change,
                    highlightbackground='#2a2d3a',
                    highlightthickness=1,
                )
                card.grid(row=row_idx, column=col_idx,
                          padx=4, pady=4, sticky='nw')
                self._cards[lt['id']] = card

    def _build_status_bar(self):
        self._status_var = tk.StringVar(value='준비')
        tk.Label(self, textvariable=self._status_var,
                 bg='#080a0e', fg='#555', font=('monospace', 8),
                 anchor='w', padx=10, pady=3
                 ).pack(fill='x', side='bottom')

    # ─────────────────────────────────────────────────────────────
    #  MQTT 연결 관리
    # ─────────────────────────────────────────────────────────────
    def _toggle_connect(self):
        global _mqtt_client, _mqtt_connected
        # 기존 연결 정리
        if _mqtt_client:
            try:
                _mqtt_client.disconnect()
                _mqtt_client.loop_stop()
            except Exception:
                pass
        _mqtt_connected = False
        self._connected = False

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=f'light-gui-{int(time.time())}')
        client.username_pw_set(_MQTT_USER, _MQTT_PASS)

        def on_connect(c, ud, flags, rc, props=None):
            global _mqtt_connected
            if rc == 0:
                _mqtt_connected = True
                self._connected = True
                self.after(0, lambda: (
                    self._conn_lbl.config(text='● MQTT 연결됨', fg='#4caf50'),
                    self._conn_btn.config(text='재연결', bg='#1a4a1a', fg='#cfc'),
                    self._status(f'MQTT 연결됨: {_BROKER}:{_BROKER_PORT}'),
                ))
            else:
                self.after(0, lambda: (
                    self._conn_lbl.config(text=f'● 연결 실패 (rc={rc})', fg='#f44'),
                    self._status(f'MQTT 연결 실패 rc={rc} — sema-host 실행 확인'),
                ))

        def on_disconnect(c, ud, rc, props=None, rc2=None):
            global _mqtt_connected
            _mqtt_connected = False
            self._connected = False
            self.after(0, lambda: (
                self._conn_lbl.config(text='● 연결 끊김', fg='#f88'),
                self._status('MQTT 연결 끊김'),
            ))

        client.on_connect    = on_connect
        client.on_disconnect = on_disconnect

        def _connect_bg():
            try:
                client.connect(_BROKER, _BROKER_PORT, keepalive=30)
                client.loop_start()
                global _mqtt_client
                _mqtt_client = client
            except Exception as e:
                self.after(0, lambda: (
                    self._conn_lbl.config(text='● 연결 실패', fg='#f44'),
                    self._status(f'MQTT 오류: {e}'),
                ))

        self._status(f'MQTT 연결 시도: {_BROKER}:{_BROKER_PORT} ...')
        threading.Thread(target=_connect_bg, daemon=True).start()

    # ─────────────────────────────────────────────────────────────
    #  프리셋 / 그룹
    # ─────────────────────────────────────────────────────────────
    def _preset_all(self, rgbw: tuple):
        r, g, b, w = rgbw
        set_all_dmx(r, g, b, w)
        for card in self._cards.values():
            card.set_rgbw(r, g, b, w)
        self._status(f'전체 프리셋  R={r} G={g} B={b} W={w}')

    def _group_preset(self, group: str, rgbw: tuple):
        r, g, b, w = rgbw
        set_group_dmx(group, r, g, b, w)
        for lt in LIGHT_MAP:
            if lt['group'] == group:
                self._cards[lt['id']].set_rgbw(r, g, b, w)

    def _group_off(self, group: str):
        set_group_dmx(group, 0, 0, 0, 0)
        for lt in LIGHT_MAP:
            if lt['group'] == group:
                self._cards[lt['id']].set_rgbw(0, 0, 0, 0)
        self._status(f'{GROUP_META[group]["label"]} OFF')

    def _on_card_change(self):
        pass  # 슬라이더가 set_light_dmx를 직접 호출하므로 여기서는 불필요

    # ─────────────────────────────────────────────────────────────
    #  Chase 테스트
    # ─────────────────────────────────────────────────────────────
    def _toggle_chase(self):
        if self._chase_running:
            self._chase_running = False
            self._chase_btn.config(text='▶ Chase', fg='#7eb8f7')
            self._status('Chase 정지')
        else:
            self._chase_running = True
            self._chase_btn.config(text='■ 정지', fg='#ff8888')
            threading.Thread(target=self._chase_worker, daemon=True).start()

    def _chase_worker(self):
        """한 번에 한 조명씩 순환 점등 (0.3s 간격)"""
        self._status('Chase 실행 중...')
        lights = LIGHT_MAP
        idx    = 0
        while self._chase_running:
            set_all_dmx(0, 0, 0, 0)
            lt = lights[idx % len(lights)]
            set_light_dmx(lt['id'], 255, 180, 0, 80)
            # GUI 업데이트는 after로 메인 스레드에서
            lid = lt['id']
            self.after(0, lambda i=lid: self._chase_highlight(i))
            time.sleep(0.35)
            idx += 1
        set_all_dmx(0, 0, 0, 0)
        self.after(0, lambda: self._preset_all_gui((0, 0, 0, 0)))

    def _chase_highlight(self, active_id: int):
        for lid, card in self._cards.items():
            if lid == active_id:
                card.set_rgbw(255, 180, 0, 80)
            else:
                card.set_rgbw(0, 0, 0, 0)

    def _preset_all_gui(self, rgbw: tuple):
        for card in self._cards.values():
            card.set_rgbw(*rgbw)

    # ─────────────────────────────────────────────────────────────
    #  주기 갱신 (0.5s)
    # ─────────────────────────────────────────────────────────────
    def _tick(self):
        self.after(500, self._tick)

    def _status(self, msg: str):
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        self._status_var.set(f'[{ts}]  {msg}')

    def _on_close(self):
        self._chase_running = False
        mqtt_publish('BRIGHTNESS:1.0')  # 밝기 복구
        mqtt_publish('CLEAR')           # 수동 오버라이드 해제
        mqtt_publish('MODE:AUTO')       # AUTO 모드 복귀
        self.destroy()                  # UI 즉시 닫기
        # MQTT 정리 — 별도 스레드에서 (loop_stop이 블로킹이므로)
        def _cleanup():
            if _mqtt_client:
                try:
                    time.sleep(0.2)
                    _mqtt_client.disconnect()
                    _mqtt_client.loop_stop()
                except Exception:
                    pass
        threading.Thread(target=_cleanup, daemon=True).start()


def _sigterm_handler(signum, frame):
    """pkill / systemd stop 등 SIGTERM 수신 시 조명 상태 복구"""
    mqtt_publish('BRIGHTNESS:1.0')
    mqtt_publish('CLEAR')
    mqtt_publish('MODE:AUTO')
    if _mqtt_client:
        try:
            time.sleep(0.2)
            _mqtt_client.loop_stop()
        except Exception:
            pass
    sys.exit(0)


# ══════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    signal.signal(signal.SIGTERM, _sigterm_handler)
    app = App()
    app.mainloop()
