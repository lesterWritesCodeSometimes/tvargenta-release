# TVArgenta Hub — Design Sketch

A content distribution and management service running on **pixie**, serving as the
canonical library and control plane for one or more TVArgenta devices.

Status: phases 0–1 implemented, 2026-07-20 (`hub/` service + `hub_agent.py` state
push). Phases 2–3 (content pull, hub-mode channels, encode CLI) not built yet.

Quickstart:

```
# en pixie (una vez):
git clone <repo> && cd tvargenta-release && sudo ./hub/install.sh
/srv/tvargenta-hub/venv/bin/python hub/manage.py set-password

# desde la Mac (seed inicial de la biblioteca):
./hub/seed_from_mac.sh

# en el dispositivo (backup de estado):
#   crear token de device en el UI (Tokens) y content/hub_agent.json, luego:
sudo ./install_hub_agent.sh
```

## Goals

- **Canonical library.** Pixie owns the master copy of all playable content and its
  shared metadata. Devices hold mirrors. Backup stops being a chore and becomes a
  property of the architecture.
- **Multi-device from day one.** Adding a device means registering it and letting it
  sync; it does not mean new plumbing.
- **Central management web UI** on pixie: browse the library, edit the global channel
  lineup, watch device health, restore device state.
- **Raw upload API** for a future command-line tool that encodes on a powerful source
  machine and pushes finished files. The web UI deliberately has **no upload**.
- **Versioned backup of per-device state** (tapes, plays, cursors, config).

## Non-goals (v1)

- Uploading or encoding in the web UI (the CLI owns ingestion).
- Raw/pre-encode capture management (`mini vhs tapes` stays out of scope).
- Per-device content subsets — the global lineup's content is mirrored identically to
  every device.
- Transcoding on the hub.
- Schedule generation — devices keep generating their own daily schedules locally
  from the lineup + library.

## Decisions log

| Question | Decision |
|---|---|
| Source of truth | Pixie is canonical; devices mirror |
| Content per device | Identical everywhere (full mirror of the library) |
| Channel lineup | One **global** lineup, edited only on the hub. A device connected to a hub loses local channel editing |
| Tapes | Per-device (must be "recorded" on that device); hub keeps versioned backups |
| Transport | Devices **pull** over HTTP (sync agent on device) |
| Upload API | Single streaming PUT + sha256 verification (no chunking) |
| Auth | Auth on everything — login-protected UI, bearer tokens on all API routes |
| Code location | `hub/` subdirectory of this repo |
| Runtime | systemd + venv on pixie, deploy = git pull + restart (same model as argentv) |

## Topology

```
 source machine (Mac)                  pixie                        devices
┌─────────────────────┐    PUT    ┌──────────────┐    pull    ┌──────────────────┐
│ encode CLI (future) │──────────▶│ tvargenta-hub │◀──────────│ argentv (+ next) │
└─────────────────────┘  content  │  web UI + API │  manifest │  sync agent      │
                                  │  library on   │  content  │  + player app    │
                       browser ──▶│  NVMe (1.8T)  │  channels │                  │
                                  └──────────────┘   ▲        └──────────────────┘
                                                     └── state snapshots (push)
```

## Storage layout on pixie

```
/srv/tvargenta-hub/
  library/
    videos/            # same relative-path scheme devices use today:
      <movie>.mp4      #   top-level movies, series/<Series>/<ep>.mp4,
      series/...       #   commercials/, system/
      commercials/
      system/
    thumbnails/
  db/hub.sqlite        # content index + device registry (path, size, sha256,
                       # metadata fields, tokens, last-seen)
  channels/            # current channels.json + timestamped history
  devices/<device_id>/
    state/<timestamp>/ # snapshots: tapes.json, plays.json, episode_cursors.json,
                       # configuracion.json, volumen.json, ...
  incoming/            # upload staging (same filesystem → atomic rename)
```

One directory tree holds everything worth backing up; a later offsite copy is just
`restic`/`rsync` of `/srv/tvargenta-hub`.

## Identity, metadata, manifest

- **Content ID stays the filename stem**, matching today's `metadata.json` keying. The
  hub additionally records each file's **sha256**, used for upload verification, sync
  verification, and change detection.
- **Shared metadata becomes hub-owned**: title, category, series, tags — the facts
  about the content itself. The CLI uploads a metadata sidecar with each video; the
  hub UI can edit it. Devices receive it and write their local `metadata.json`.
- **Device-local data stays local** (and gets backed up as opaque snapshots):
  `tapes.json`, `plays.json`, `episode_cursors.json`, `channel_detection_cache.json`,
  schedules.
- The **manifest** is the sync contract:

```json
GET /api/v1/manifest
{
  "library_version": 41,
  "channels_version": 7,
  "metadata_version": 12,
  "files": [
    {"path": "videos/series/Little_Bear/....mp4", "size": 123456789, "sha256": "..."},
    {"path": "thumbnails/....jpg", "size": 4321, "sha256": "..."}
  ]
}
```

Versions are monotonic integers bumped on any change; agents poll cheaply with
`If-None-Match`/ETag and only walk the file list when something changed.

## API sketch

All routes require `Authorization: Bearer <token>`. Two token classes, issued and
revocable from the hub UI:

- **admin** — full access (the CLI, and the UI's own backend)
- **device** — one per device; can read manifest/content/channels/metadata and write
  only its own state snapshots and heartbeat

| Route | Who | Purpose |
|---|---|---|
| `GET  /api/v1/manifest` | device, admin | Sync contract above; doubles as heartbeat (hub records last-seen per token) |
| `GET  /api/v1/content/<path>` | device, admin | Download; supports `Range` for resume |
| `PUT  /api/v1/content/<path>` | admin | Raw upload. Header `X-Content-SHA256`; hub streams to `incoming/`, verifies hash, atomically renames into `library/`. Mismatch → 422, nothing changes. Re-PUT of an identical hash → 200 no-op (idempotent) |
| `DELETE /api/v1/content/<path>` | admin | Remove from library (refuses, or warns via UI, if a channel's `series_filter`/tags still reference it) |
| `GET/PUT /api/v1/metadata` | device r / admin rw | Shared per-title metadata; PUT bumps `metadata_version` |
| `GET/PUT /api/v1/channels` | device r / admin rw | The global lineup (today's `canales.json` schema); PUT archives the old version and bumps `channels_version` |
| `POST /api/v1/devices/<id>/state` | that device | Multipart/JSON state snapshot; hub stores under `devices/<id>/state/<ts>/`, prunes by retention policy |
| `GET  /api/v1/devices` | admin | Registry + health for the dashboard |

## Web UI

Flask + Jinja, same stack and idioms as the device app. Login-protected (single admin
account is fine for v1).

1. **Library** — thumbnail grid, filter by series/category/tag, item detail with
   editable metadata, delete (with channel-reference warning). Read-only with respect
   to files: no upload affordance anywhere.
2. **Channels** — the global lineup editor: number, nombre, aliases, `series_filter`,
   `tags_prioridad`, icon. Functionally a port of the device's channel editing UI;
   "publish" bumps `channels_version` and devices pick it up on next poll.
3. **Devices** — registered devices with last-seen, library/channels versions they've
   reached, disk headroom (reported in state pushes), snapshot history with download
   and "restore this snapshot" (device agent applies it on next poll).
4. **Tokens** — issue/revoke admin and device tokens.

## Device sync agent

A small daemon (or systemd timer) added to this repo and run on each device:

1. Poll `GET /manifest` with ETag. On change: download missing/changed files to a
   temp path, verify sha256, atomically move into `content/`; delete local files no
   longer in the manifest (only within hub-managed dirs — never touches tapes,
   plays, caches).
2. Apply hub-provided channels + metadata by writing `canales.json` /
   `metadata.json`, then nudge the app to reload.
3. Push a state snapshot (tapes, plays, cursors, config, volumen) on a daily timer
   and shortly after any of those files change.
4. **Hub mode**: a settings flag, set when the device is enrolled. When on, the
   device UI hides/disables channel editing (the hub owns the lineup).

## Migration plan

- **Phase 0 — seed.** Move pixie's existing `argentv_sync` mirror into
  `library/videos/` (it's already verified identical to the device), copy thumbnails
  + `metadata.json` from argentv, import argentv's `canales.json` as the initial
  global lineup.
- **Phase 1 — backup complete.** Hub serves read-only UI; device agent only pushes
  state snapshots. *This closes today's backup gap (state + thumbnails were 0%
  backed up) before any risky behavior change.*
- **Phase 2 — hub takes over.** Agent starts pulling content + channels; device
  enters hub mode; the manual rsync retires.
- **Phase 3 — ingestion.** Encode CLI ships, uploading via PUT; new content flows
  source machine → hub → devices with no hands on the Pi.

## Open questions (not yet decided)

1. **Thumbnails at ingest** — should the CLI upload a thumbnail alongside each video,
   or should the hub generate one with ffmpeg on upload? (Hub-side generation keeps
   the CLI dumber and styling consistent; pixie has the horsepower.)
2. **Device enrollment ergonomics** — manual token paste into a config file on the
   device, or a short-lived pairing code shown in the hub UI?
3. **Restore flow safety** — restoring `tapes.json` onto a device that has since
   recorded new tapes; probably "agent applies snapshot only after explicit confirm
   in hub UI + device idle."
4. **Commercials/system videos** — treated as ordinary library content (current
   assumption), or a separate tier with their own UI section?
