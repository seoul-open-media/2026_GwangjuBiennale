#!/usr/bin/env bash
# GB16 컨트롤 페이지 자동 열기 (서비스 준비 대기)
CONTROL_URL="http://localhost:8182/gb16_control.html"
MAX_WAIT=60

echo "[..] GB16 컨트롤 페이지 대기 중..."
for i in $(seq 1 $MAX_WAIT); do
    if wget -q --spider "$CONTROL_URL" 2>/dev/null; then
        break
    fi
    sleep 1
done

# Chromium/Chrome 우선, 없으면 Firefox
CHROME_FLAGS="--disable-gpu --disable-software-rasterizer --disable-infobars --disable-session-crashed-bubble --no-first-run --no-default-browser-check --noerrdialogs --disable-translate --user-data-dir=/tmp/gb16-chrome-profile"

if command -v google-chrome &>/dev/null; then
    exec google-chrome $CHROME_FLAGS "$CONTROL_URL"
elif command -v chromium-browser &>/dev/null; then
    exec chromium-browser $CHROME_FLAGS "$CONTROL_URL"
elif command -v firefox &>/dev/null; then
    exec firefox "$CONTROL_URL"
fi
