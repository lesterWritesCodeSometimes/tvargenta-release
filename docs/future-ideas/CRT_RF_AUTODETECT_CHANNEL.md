# Future idea: CRT over RF with auto-detected channel changes (single-channel agile transmitter)

Status: idea only, not started. Captured 2026-07-20 from a design discussion.

## The concept

Drive a real CRT TV (the Sony Trinitron KV-27FS100L by the couch) over its
antenna input instead of composite/HDMI, and let the TV's own channel
buttons do the channel changing. The Pi transmits **one RF channel at a
time** through an agile modulator. When the viewer changes channels on the
TV, we detect which channel the TV's tuner moved to, retune our transmitter
to that frequency, and switch the player to that channel's timeline. The
simulated simultaneous broadcast stays simulated — but the TV experience is
fully real, including a moment of dead air while we chase the tuner.

## Why detection is possible: local oscillator leakage

- A superheterodyne tuner runs a local oscillator (LO) at
  `picture carrier + 45.75 MHz` (NTSC intercarrier IF). A small amount of
  LO signal leaks *backwards out of the antenna jack*.
- On a closed coax run this is sniffable: put a splitter or (better) a
  directional coupler in the antenna line and hang an RTL-SDR off the tap.
  Whichever candidate LO frequency shows a carrier = the tuned channel.
- Impedance/load sensing does **not** work — the tuner front end is a
  broadband ~75 Ω load regardless of tuned channel. LO leakage is the only
  passive signal available without opening the TV.
- Prior art: this exact technique was commercially deployed in audience
  metering on consumer sets of this era. See Nielsen-family patents
  US 7,590,991 ("Method and apparatus for determining channel to which a
  TV or VCR is tuned"), US 5,404,161 and US 5,510,859. The patents note the
  leakage can sit below the noise floor and still be recovered — with an
  SDR we do the same by narrowband FFT at the ~12 known candidate LO
  frequencies with a few hundred ms of coherent integration, instead of
  scanning blind. UK "TV detector vans" are the folklore version (radiated
  detection at a distance; we have a direct cable tap, which is far easier).

### LO frequency table (NTSC broadcast VHF, LO = picture carrier + 45.75 MHz)

| Ch | Picture carrier (MHz) | LO (MHz) |
|----|----------------------|----------|
| 2  | 55.25  | 101.00 |
| 3  | 61.25  | 107.00 |
| 4  | 67.25  | 113.00 |
| 5  | 77.25  | 123.00 |
| 6  | 83.25  | 129.00 |
| 7  | 175.25 | 221.00 |
| 8  | 181.25 | 227.00 |
| 9  | 187.25 | 233.00 |
| 10 | 193.25 | 239.00 |
| 11 | 199.25 | 245.00 |
| 12 | 205.25 | 251.00 |
| 13 | 211.25 | 257.00 |

All LOs fall inside an RTL-SDR's range. If the TV is used in Cable mode the
candidate table changes to the CATV plan but the method is identical.

## What we know about this specific TV (KV-27FS100L)

- 2003 North American model, NTSC-only, BA-6 chassis, FD Trinitron WEGA
  (despite the "L" suffix it is NOT a Latin-America PAL-N variant —
  confirmed via crtdatabase.com). Cheap US NTSC modulators and the Pi's
  composite out in NTSC mode work as-is.
- PLL frequency-synthesis tuner controlled over I2C by the main micro.
  Well-shielded by 2003 FCC standards, so LO leakage will be much weaker
  than on a vintage set. **Detectability is the first thing to verify** —
  no one online has published LO measurements for this model.
- Blue-screen mute on loss of sync (not RF snow). The detect-and-retune gap
  will read as a blue flash. Some WEGAs can disable the blue screen in the
  service menu if real snow is wanted.
- Channel memory: front-panel CH+/- only steps through channels stored by
  Auto Program, and the scan skips dead frequencies. During setup, transmit
  on each lineup channel while the scan runs (step the agile modulator
  through the list), or add channels manually via remote direct entry.
- Service manual is on ManualsLib (search "Sony Trinitron KV-27FS100").

## Architecture

```
Pi (player, one channel at a time)
 ├─ composite + audio ──> agile modulator (CATV headend gear, channel set by Pi)
 │                          │
 │                          v
 │                    directional coupler ──> coax ──> KV-27FS100L antenna in
 │                          │ (tap, oriented toward TV-originated signals)
 └─ USB <── RTL-SDR V4 <────┘  (+ a few dB attenuation)
```

Control loop: RTL-SDR continuously integrates at the candidate LO
frequencies → LO moves → look up new channel → command modulator to that
channel's frequency → seek player to that channel's live timeline position.
The channel-change event enters the app exactly where a keypress/encoder
event does today; the rest of the player logic is unchanged.

## Detection-side notes

- RTL-SDR Blog V4 (~$35): TCXO (stable enough to disambiguate LOs),
  first-class Linux/Pi support. `rtl_power` or a small custom FFT tool.
- Our own transmitter is on the same cable, tens of dB stronger than the
  LO leak. Mitigate with directional coupler orientation + attenuation
  before reaching for a higher-dynamic-range SDR (Airspy Mini, RSP1B).
  Also: the transmitter frequency is always known, so its energy can be
  masked out in software.

## Transmit-side options

1. **Used CATV agile modulator** (eBay, analog headend gear): takes
   composite + audio, outputs on any commanded channel. Zero CPU, solid
   signal. Check how it's commanded (front panel vs serial) — a
   serial-controllable unit is ideal.
2. **HackRF One + hacktv** (github.com/fsphil/hacktv): fully
   software-defined NTSC, retunes instantly. ~$340 genuine (avoid clones).
   Half-duplex, so the RTL-SDR still does the listening. Real-time NTSC
   synthesis wants a Pi 5 or the Beelink.

## Fallback if LO leakage is too weak: I2C tap

Open the set, level-shift SDA/SCL at the tuner can (5 V logic), sniff the
PLL divider word to get the exact tuned frequency deterministically.
Solder-two-wires job with the service manual open. Ties the rig to this
one TV, but it cannot fail to work.

## Build order

1. Splitter + RTL-SDR on the antenna line; flip channels by hand and
   confirm the LO is visible per channel. (~$50, answers the only real
   unknown. If marginal, increase FFT integration time before giving up.)
2. Add agile modulator; store the lineup in the TV's channel memory.
3. Wire the sniff → retune → seek loop into the player.
4. If step 1 fails outright: I2C tap.

Rough BOM: RTL-SDR V4 ~$35, directional coupler/splitter + attenuators
~$15, used agile modulator ~$20–60. Under $100 unless HackRF route.

## Sibling idea (parked separately): true multi-channel simulcast

Broadcast every channel simultaneously on its own frequency so no detection
is needed at all — the simulated simulcast becomes real. Needs one baseband
feed per channel (a Pi drives at most 2 via dual HDMI→composite
converters), or RF-domain synthesis of the whole band from one wideband
SDR. The Beelink SER5 Pro (Ryzen 7 5825U) by the TV has ample CPU for a
multi-channel NTSC multiplex; bladeRF 2.0 micro xA4 (56 MHz BW, tunes down
to 47 MHz, 12-bit DACs, ~$600 genuine) covers up to 9 contiguous channels —
notably the whole high-VHF block ch 7–13 (174–216 MHz), i.e. the classic
Buenos Aires dial (7, 9, 11, 13) from one device. A low channel (e.g. 2)
would need a $20 fixed-modulator sidecar into the combiner. Software gap:
nothing off-the-shelf does multi-channel NTSC; plan is N hacktv instances
writing baseband IQ to pipes + a small combiner that shifts, sums, and
streams to the SDR (FIFO backpressure rate-locks the instances for free).
8-bit DACs (HackRF) lose ~9.5 dB across 3 carriers → faint snow; 12-bit
solves it. The single-channel auto-detect idea above is the cheap first
step and shares the modulator/coax plumbing.
