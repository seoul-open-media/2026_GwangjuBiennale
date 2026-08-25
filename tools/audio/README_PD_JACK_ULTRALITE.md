# Pure Data + JACK (PipeWire) + UltraLite mk5

## 1) One-time install (requires sudo)

```bash
sudo apt update
sudo apt install -y pipewire-jack qpwgraph
```

## 2) Start Pure Data through JACK

From repository root:

```bash
./tools/audio/start_pd_ultralite_pwjack.sh
```

Default behavior now:
- Opens `10` output channels (`output_1` ... `output_10`)
- Auto-applies channel mapping for your setup:
	- `output_3 -> playback_AUX0` (physical 1)
	- `output_4 -> playback_AUX1` (physical 2)
	- `output_1 -> playback_AUX2`
	- `output_2 -> playback_AUX3`
	- `output_5..10 -> playback_AUX4..9`

If your patch has program mostly on `output_3/4` and channels 4~8 sound empty,
launch with mirrored mode (copies 3/4 to all 1~10):

```bash
PD_ROUTE_MODE=mirror34 ./tools/audio/start_pd_ultralite_pwjack.sh
```

You can also apply it on a running session:

```bash
./tools/audio/pd_route_mirror34_to_1_10.sh
```

Open a patch directly:

```bash
./tools/audio/start_pd_ultralite_pwjack.sh PD/GB16/Light/sequencer.pd
```

## 3) Connect ports

Run patchbay:

```bash
qpwgraph
```

Connect:
- Pure Data output 1/2 -> UltraLite-mk5 playback 1/2
- UltraLite-mk5 capture 1/2 -> Pure Data input 1/2 (if needed)

If routing gets messy (for example only one side is audible), force clean stereo routing:

```bash
./tools/audio/pd_route_stereo_ultralite.sh
```

This keeps only:
- Pure Data output_1 -> UltraLite playback_AUX0
- Pure Data output_2 -> UltraLite playback_AUX1

To re-apply the 10ch mapping (3/4 -> physical 1/2):

```bash
./tools/audio/pd_route_10ch_ultralite_swap34_to12.sh
```

## 4) Tune latency

You can override defaults at launch:

```bash
PD_RATE=48000 PD_AUDIOBUF=12 ./tools/audio/start_pd_ultralite_pwjack.sh
```

If you hear crackles, increase `PD_AUDIOBUF` to 16 or 20.
