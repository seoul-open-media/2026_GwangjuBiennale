#!/usr/bin/env bash
set -euo pipefail

if ! command -v pw-link >/dev/null 2>&1; then
  echo "[ERROR] pw-link not found."
  exit 1
fi

out_list="$(pw-link -oI)"
in_list="$(pw-link -iI)"

get_id_from_list() {
  local list="$1"
  local pat="$2"
  awk -v p="$pat" '$0 ~ p {print $1; exit}' <<< "$list"
}

# Disconnect default one-to-one links output_1..10 -> AUX0..9 if present
for n in 0 1 2 3 4 5 6 7 8 9; do
  out_id="$(get_id_from_list "$out_list" "Pure Data:output_$((n+1))$")"
  in_id="$(get_id_from_list "$in_list" "UltraLite-mk5.*playback_AUX${n}$")"
  if [[ -n "${out_id}" && -n "${in_id}" ]]; then
    pw-link -d "${out_id}" "${in_id}" >/dev/null 2>&1 || true
  fi
done

# Requested mapping:
# 3/4 signal should come out on physical 1/2, while outputs 1..10 remain active
# output_3->AUX0, output_4->AUX1, output_1->AUX2, output_2->AUX3, output_5..10->AUX4..9
map_pairs=("3:0" "4:1" "1:2" "2:3" "5:4" "6:5" "7:6" "8:7" "9:8" "10:9")
for pair in "${map_pairs[@]}"; do
  out_ch="${pair%%:*}"
  aux_ch="${pair##*:}"
  out_id="$(get_id_from_list "$out_list" "Pure Data:output_${out_ch}$")"
  in_id="$(get_id_from_list "$in_list" "UltraLite-mk5.*playback_AUX${aux_ch}$")"
  if [[ -n "${out_id}" && -n "${in_id}" ]]; then
    pw-link "${out_id}" "${in_id}" >/dev/null 2>&1 || true
  fi
done

echo "[OK] Applied mapping: 3->1, 4->2, 1->3, 2->4, 5->5, ..., 10->10"
