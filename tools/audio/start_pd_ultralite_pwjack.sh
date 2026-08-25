#!/usr/bin/env bash
set -euo pipefail

RATE="${PD_RATE:-48000}"
AUDIOBUF="${PD_AUDIOBUF:-12}"
IN_CH="${PD_IN_CH:-2}"
OUT_CH="${PD_OUT_CH:-10}"
AUTO_ROUTE="${PD_AUTO_ROUTE:-1}"
ROUTE_MODE="${PD_ROUTE_MODE:-swap34to12}"
PATCH_FILE="${1:-/home/allmedia/Documents/GitHub/2026_GwangjuBiennale/PD/GB16/Sequencer/GB16_DMXUSBPRO.pd}"

if ! command -v pd >/dev/null 2>&1; then
  echo "[ERROR] Pure Data (pd) is not installed."
  echo "Install: sudo apt install -y puredata"
  exit 1
fi

if ! command -v pw-jack >/dev/null 2>&1; then
  echo "[ERROR] pw-jack is not installed."
  echo "Install: sudo apt install -y pipewire-jack"
  exit 1
fi

echo "[INFO] Starting Pure Data with JACK via PipeWire"
echo "[INFO] rate=${RATE}, audiobuf=${AUDIOBUF}, in=${IN_CH}, out=${OUT_CH}"

if [[ "${AUTO_ROUTE}" == "1" ]]; then
  (
    for _ in $(seq 1 40); do
      if pw-link -o | grep -q "Pure Data:output_10"; then
        case "${ROUTE_MODE}" in
          mirror34)
            "$(dirname "$0")/pd_route_mirror34_to_1_10.sh" >/dev/null 2>&1 || true
            ;;
          swap34to12|*)
            "$(dirname "$0")/pd_route_10ch_ultralite_swap34_to12.sh" >/dev/null 2>&1 || true
            ;;
        esac
        exit 0
      fi
      sleep 0.2
    done
  ) &
fi

if [[ -n "${PATCH_FILE}" ]]; then
  exec pw-jack pd -jack -r "${RATE}" -audiobuf "${AUDIOBUF}" -inchannels "${IN_CH}" -outchannels "${OUT_CH}" -open "${PATCH_FILE}"
else
  exec pw-jack pd -jack -r "${RATE}" -audiobuf "${AUDIOBUF}" -inchannels "${IN_CH}" -outchannels "${OUT_CH}"
fi
