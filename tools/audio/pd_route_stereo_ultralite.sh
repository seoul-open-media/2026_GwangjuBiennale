#!/usr/bin/env bash
set -euo pipefail

if ! command -v pw-link >/dev/null 2>&1; then
  echo "[ERROR] pw-link not found. Install pipewire-jack."
  exit 1
fi

out_list="$(pw-link -oI)"
in_list="$(pw-link -iI)"

get_id_from_list() {
  local list="$1"
  local name="$2"
  awk -v pat="$name" '$0 ~ pat {print $1; exit}' <<< "$list"
}

pd_out_1="$(get_id_from_list "$out_list" 'Pure Data:output_1$')"
pd_out_2="$(get_id_from_list "$out_list" 'Pure Data:output_2$')"
ul_in_1="$(get_id_from_list "$in_list" 'UltraLite-mk5.*playback_AUX0$')"
ul_in_2="$(get_id_from_list "$in_list" 'UltraLite-mk5.*playback_AUX1$')"

if [[ -z "${pd_out_1}" || -z "${pd_out_2}" ]]; then
  echo "[ERROR] Pure Data output_1/output_2 ports not found."
  echo "Start Pure Data first, then run this script."
  exit 1
fi

if [[ -z "${ul_in_1}" || -z "${ul_in_2}" ]]; then
  echo "[ERROR] UltraLite mk5 playback AUX0/AUX1 ports not found."
  exit 1
fi

for ch in 3 4 5 6 7 8 9 10; do
  pd_out_n="$(get_id_from_list "$out_list" "Pure Data:output_${ch}$")"
  ul_in_n="$(get_id_from_list "$in_list" "UltraLite-mk5.*playback_AUX$((ch-1))$")"
  if [[ -n "${pd_out_n}" && -n "${ul_in_n}" ]]; then
    pw-link -d "${pd_out_n}" "${ul_in_n}" >/dev/null 2>&1 || true
  fi
done

pw-link "${pd_out_1}" "${ul_in_1}" >/dev/null 2>&1 || true
pw-link "${pd_out_2}" "${ul_in_2}" >/dev/null 2>&1 || true

echo "[OK] Stereo routing set"
echo "  Pure Data:output_1 -> UltraLite playback_AUX0"
echo "  Pure Data:output_2 -> UltraLite playback_AUX1"
