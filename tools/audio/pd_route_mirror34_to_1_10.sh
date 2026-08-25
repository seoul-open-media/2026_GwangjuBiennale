#!/usr/bin/env bash
set -euo pipefail

if ! command -v pw-link >/dev/null 2>&1; then
  echo "[ERROR] pw-link not found."
  exit 1
fi

# Optional: normalize device output gain
if command -v wpctl >/dev/null 2>&1; then
  # 127 is UltraLite sink in this setup; ignore if different
  wpctl set-volume 127 1.00 >/dev/null 2>&1 || true
fi

out_list="$(pw-link -oI)"
in_list="$(pw-link -iI)"

get_id_from_list() {
  local list="$1"
  local pat="$2"
  awk -v p="$pat" '$0 ~ p {print $1; exit}' <<< "$list"
}

# Remove any existing PD->AUX0..AUX9 links
for aux in 0 1 2 3 4 5 6 7 8 9; do
  in_id="$(get_id_from_list "$in_list" "UltraLite-mk5.*playback_AUX${aux}$")"
  [[ -z "${in_id}" ]] && continue
  for out_ch in 1 2 3 4 5 6 7 8 9 10; do
    out_id="$(get_id_from_list "$out_list" "Pure Data:output_${out_ch}$")"
    [[ -n "${out_id}" ]] && pw-link -d "${out_id}" "${in_id}" >/dev/null 2>&1 || true
  done
done

# Mirror stereo from output_3/output_4 to AUX0..AUX9
out3="$(get_id_from_list "$out_list" 'Pure Data:output_3$')"
out4="$(get_id_from_list "$out_list" 'Pure Data:output_4$')"

if [[ -z "${out3}" || -z "${out4}" ]]; then
  echo "[ERROR] Pure Data output_3/output_4 not found. Start PD first."
  exit 1
fi

for aux in 0 2 4 6 8; do
  in_id="$(get_id_from_list "$in_list" "UltraLite-mk5.*playback_AUX${aux}$")"
  [[ -n "${in_id}" ]] && pw-link "${out3}" "${in_id}" >/dev/null 2>&1 || true
done

for aux in 1 3 5 7 9; do
  in_id="$(get_id_from_list "$in_list" "UltraLite-mk5.*playback_AUX${aux}$")"
  [[ -n "${in_id}" ]] && pw-link "${out4}" "${in_id}" >/dev/null 2>&1 || true
done

echo "[OK] Mirrored PD output_3/4 to UltraLite AUX0..9"
