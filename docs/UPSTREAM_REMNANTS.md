# Upstream Remnants — Unused & Broken Feature Leftovers

This repo is a fork of [rsappia/TVArgenta-Release](https://github.com/rsappia/TVArgenta-Release)
(fork point: upstream commit `aa6232a`, 2025-11-26). The fork substantially changed the
featureset — replacing the generic upload / tags / configuración pages with inline and
specialized flows, adding the scheduler, VCR/NFC, metadata daemon, and channel detection.
That rework left behind a number of upstream remnants that are now unused or actively broken.

This document catalogs all of them, with evidence.

## Resolution log (2026-07-17)

The cleanup was executed piece by piece; the findings below are kept as the record of
what existed and why it was removed. Commits:

| Commit | Piece |
|---|---|
| `d96ec6f` | Dead pre-fork Python modules (encoder_test, encoder_menu, analyze_loudness) + dead app.py imports/function (§3.1, §3.2) |
| `af910f3` | Broken /tags + /configuracion routes, POST endpoints, i18n bundles, editor links (§1.1, §1.2, §2.1, §2.2) |
| `2a42fac` | Superseded generic /upload page + /upload_status + upload_* i18n (§2.3) |
| `ca804d6` | kiosk_boot.html, unused Splash/static/assets files, redundant .rar (§2.5, §4) |
| `8b98878` | Orphan routes: /api/videos, /delete_full, /static-intro.mp4, /editar_canal, /admin (§3.3) |
| `c51fc6f` | Unimplemented OSD menu stubs "Predefined/My Channels" (§6.3) |
| `6c6e4f7` | Legacy tag-based picker + plays/fairness tracking (§6.1, §6.2) |
| `70908de` | Tag vocabulary/config layer, editor tag+modes UI, dead channel keys, upstream seed data (§6.4, §6.6) |
| `ca1a631` | Stale upstream README.en.md (§5.1) |

Deliberately kept: `/upload/commercials` GET page (hosts the channel-earmarking UI,
reached by direct URL), `/vcr_admin` (functional hidden admin page), `/video/<id>`
(linked from the commercials page), `/index` redirect alias (used by the nav),
`docs/VCR_NFC_IMPLEMENTATION_PLAN.md` (historical design notes), the gaming-toggle
scripts' stale `splash.png` reference (Pi-side `fbv` cosmetics, guarded by `[ -f ]`),
and inert `"tags"`/`"modo"` fields in existing video metadata.

Legend: **BROKEN** = live code path that fails at runtime · **DEAD** = unreachable/unreferenced ·
**STALE** = docs/archives that no longer match reality.

---

## 1. Actively broken (user-visible failures)

These are the highest priority: real code paths that error when exercised.

### 1.1 `/tags` route renders a deleted template — reachable from the UI
- `app.py:1440` does `render_template("tags.html", ...)`, but `templates/tags.html` was
  **deleted by the fork** (upstream had it; see `git diff --diff-filter=D aa6232a HEAD`).
  Hitting `/tags` raises `TemplateNotFound` → HTTP 500.
- **Still linked from the editor UI** in three places, so users can actually hit this 500:
  - `templates/edit.html:48` — "🛠 Tags" link
  - `templates/edit.html:219` — "🛠 Editar grupos y tags" link
  - `templates/edit.html:231` — "🛠 Tags" button

### 1.2 `/configuracion` route renders a deleted template
- `app.py:1595` does `render_template("configuracion.html", ...)`, but
  `templates/configuracion.html` was also deleted by the fork. `/configuracion` → 500.
- No inbound links remain (orphan + broken), so it only fails if typed manually.

### 1.3 Gaming-toggle scripts reference a nonexistent splash image
- `Config_files/servicios_y_scripts_toggle_tva_games/usr/local/bin/return_to_tvargenta.sh:4`
  and `enter-gaming-wrapper.sh:4` point `fbv` at `/srv/tvargenta/Splash/splash.png`.
  No `splash.png` exists (the repo ships `splash_1.png`–`splash_5.png`). The `[ -f "$IMG" ]`
  guard makes this fail silently — the transition splash simply never shows.

---

## 2. Dead subsystems (route + template + i18n clusters)

Each of these is a coherent upstream feature whose UI was removed but whose backend
plumbing survives.

### 2.1 Tags editor (UI deleted; backend routes and i18n orphaned)
The tag *data* is still live (`tags.json` feeds the scheduler and edit page), but the
standalone tag-management UI is gone. Left behind:
- Broken route `/tags` (`app.py:1436`) — see §1.1.
- Five POST endpoints whose only UI was the deleted `tags.html`:
  - `/add_tag` (`app.py:1442`)
  - `/update_group_color` (`app.py:1479`)
  - `/add_group` (`app.py:1494`)
  - `/delete_tag` (`app.py:1506`)
  - `/delete_group` (`app.py:1544`)
- i18n bundles `templates/i18n/tags_{es,en,de}.json` — mapped at `app.py:4213` but the
  target page 500s, so these strings can never be shown.

### 2.2 Configuración page (UI deleted; backend route and i18n orphaned)
- Broken route `/configuracion` (`app.py:1583`) — see §1.2.
- `/guardar_configuracion` (`app.py:1599`) — the form that posted to it lived in the
  deleted `configuracion.html`.
- i18n bundles `templates/i18n/configuracion_{es,en,de}.json` — mapped at `app.py:4216`,
  unreachable.

### 2.3 Generic upload page (superseded by specialized uploaders)
- `templates/upload.html` is rendered by `/upload` (`app.py:1370-1373`) but **nothing links
  to it** — the fork replaced it with inline upload in `index.html` plus
  `upload_series.html` / `upload_commercials` flows.
- i18n bundles `templates/i18n/upload_{es,en,de}.json` (mapped at `app.py:4219`) are only
  merged when the orphan page is hit.

### 2.4 Commercials upload page (superseded by inline uploader)
- `templates/upload_commercials.html` is rendered by GET `/upload/commercials`
  (`app.py:2312, 2340`) but no UI navigates there; commercials upload happens inline in
  `index.html:1118-1128` (which POSTs to the same endpoint — the POST handler is live,
  only the standalone GET page is orphaned).
- Side effect: `templates/video.html`'s only inbound link is
  `upload_commercials.html:231`, so `/video/<id>` (`app.py:1198`) is effectively
  orphaned along with it.

### 2.5 Kiosk boot page
- `templates/kiosk_boot.html` is **never rendered** — no `render_template`, href, or
  service file references it anywhere. (Untouched upstream file.)
- Knock-on effect: `Splash/splash_2.png` is referenced *only* by
  `kiosk_boot.html:186`, so it is effectively unused too (see §4).

---

## 3. Dead Python code

### 3.1 Whole modules (zero references anywhere)
| Module | Why it's dead |
|---|---|
| `encoder_test.py` | Standalone `RPi.GPIO` rotary-encoder probe. The project moved to the C binary `encoder_reader` (compiled in `install.sh:658-683` with libgpiod) + `tvargenta_encoder.py`. No import, subprocess, or service references it. |
| `encoder_menu.py` | Defines `EncoderHandler`, imported by nothing. Same pre-fork `RPi.GPIO` approach as above. |
| `analyze_loudness.py` | Manual one-shot LUFS analyzer, superseded by the background daemon (`metadata_daemon.py:417 analyze_loudness()`), which is what actually populates `loudness_lufs`. Not wired into any script or service; runnable only by hand. |

`RPi.GPIO` is used *only* by `encoder_test.py` and `encoder_menu.py`, confirming both are
relics of the pre-fork GPIO approach.

### 3.2 Dead code inside `app.py`
| Item | Location | Note |
|---|---|---|
| `import base64` | `app.py:27` | Never used. |
| `ROOT_DIR` import | `app.py:31` | Imported from settings, never used. |
| `VCR_STATE_FILE` import | `app.py:35` | Never used — VCR state goes through `vcr_manager.load_vcr_state()`. |
| `TAPES_FILE` import | `app.py:36` | Never used — tape data goes through `vcr_manager` helpers. |
| `analyze_loudness(filepath)` | `app.py:932` | Defined, never called. The daemon's version supersedes it. |

### 3.3 Orphan routes (defined, unreachable from any UI or hardware script)
Verified against every `fetch()`/`href`/`url_for` in templates and all hardware daemons
(only `tvargenta_encoder.py` makes HTTP calls, to `/api/power`; the others use trigger files):

| Route | Location | Note |
|---|---|---|
| `/api/videos` | `app.py:1287` | No caller anywhere. |
| `/delete_full/<id>` | `app.py:1319` | No caller. |
| `/editar_canal/<id>` | `app.py:2791` | Only appears in the i18n endpoint map (`app.py:4211`). |
| `/static-intro.mp4` | `app.py:3115` | No reference. |
| `/admin` | `app.py:3169` | Convenience redirect alias to `/gestion`; unlinked. |
| `/vcr_admin` | `app.py:3849` | Renders `vcr_admin.html` fine, but nothing links or QRs to it — reachable only by typing the URL. Verify whether it's an intentional hidden admin page before removing. |

Minor: the nav "Library" link uses `url_for('index')`, so every click goes through the
`/index` → `/gestion` redirect hop (`app.py:3164`).

---

## 4. Unused assets

| File(s) | Evidence |
|---|---|
| `static/tailwind.css` | **0 bytes**, unreferenced — every template loads Tailwind from `https://cdn.tailwindcss.com` (e.g. `base.html:12`). |
| `static/sortable.min.js` | **0 bytes**, unreferenced — `edit.html:13` loads SortableJS from jsdelivr CDN instead. |
| `Splash/splash_screen_TVA.mp4` (10.7 MB) | Never served. The app serves splash video only from `Splash/videos/` matching `splash_*.mp4` (`settings.py:37`, `app.py:168-197, 3039-3041`). |
| `Splash/splash_1.png`, `splash_3.png`, `splash_4.png`, `splash_5.png` | No references anywhere. |
| `Splash/splash_2.png` | Referenced only by the never-rendered `kiosk_boot.html:186` — effectively unused (§2.5). |
| `assets/fuentes/perfect_dos_vga_437.ttf` | The entire `assets/` tree is unreferenced by any .py/.html/.css/.sh. |
| `Config_files/servicios_y_scripts_toggle_tva_games/servicios_y_scripts_toggle_tva_games.rar` | Redundant 30 KB archive of the already-extracted `etc/`, `opt/`, `srv/`, `usr/` trees sitting next to it; nothing reads it (`install.sh` uses the extracted files). |

---

## 5. Stale documentation

### 5.1 `README.en.md` — upstream's readme, describes a repo that no longer exists
| Claim / path | Reality |
|---|---|
| `TVArgenta_v2.0.sha256` / `.asc` download-verification files | Not in repo. |
| `cd /srv/tvargenta/software/app/native` | No `native/` dir; `encoder_reader.c` lives at repo root. |
| `chmod +x scripts/*.sh` | No `scripts/` dir. |
| `python main.py` | No `main.py`; the entrypoint is `app.py`. |
| Splash path `/srv/tvargenta/software/app/assets/Splash/videos` | Actual path is `/srv/tvargenta/Splash/videos` (`settings.py:37`); `assets/` contains only fonts. |

(The Spanish `README.md` was rewritten by the fork and is accurate.)

### 5.2 Other stale docs
- `Config_files/servicios_y_scripts_toggle_tva_games/README.txt` — instructs restoring
  `tvargenta_es_switch_core_2025-10-25_0045.tar.gz`, which is not in the repo (and doesn't
  match the `.rar` that is).
- `docs/VCR_NFC_IMPLEMENTATION_PLAN.md` — a planning doc for a feature that has since
  **shipped** (every component it lists exists and is wired). Harmless, but it reads as a
  TODO when it's actually done; keep only as historical design notes.

---

## 6. Conceptual leftovers (working code serving a retired design)

These aren't broken or unreachable — they're subsystems whose *purpose* belongs to the
upstream design and no longer does anything meaningful under the fork's broadcast-scheduler
model.

### 6.1 The tag-based channel engine (superseded by the scheduler)
Upstream had no scheduler; channels were defined by tags. The fork kept the whole engine
but every deployed channel now uses `series_filter` (broadcast mode), which branches into
`scheduler.py` at `app.py:1658` and ignores tags entirely (`scheduler.py` contains zero
tag references; commercials are picked by `category == "commercial"` + per-commercial
`channels` earmarks).

The legacy engine — the entire non-broadcast half of `/api/next_video`
(`app.py:~1690-1900`: `tags_incluidos` gating, `tags_prioridad` scoring, tag-overlap
variety penalty, `min_gap_minutes`, sticky/cooldown/pending-pick machinery,
`shown_videos_por_canal`) — only executes for a channel with **no** series filter, and a
fresh one of those errors out ("No hay tags incluidos") unless tags are configured by hand.

Related latent bug: `guardar_canal` (`app.py:2736`) reads `tags_prioridad` via
`request.form.getlist`, but the `canales.html` form has no such field — **saving any
channel through the UI silently wipes its `tags_prioridad` to `[]`**.

### 6.2 Plays/fairness tracking is now write-only telemetry
`player.html` reports every video via `/api/played` (10-second or 30%-watched threshold,
`player.html:1275-1300`) into `plays.json` — but the only *reader* of plays data is the
legacy fairness calculation (`app.py:1076-1080, 1815`) inside the tag-based picker above.
On a broadcast-only deployment, plays.json accrues forever and influences nothing.

### 6.3 OSD menu: "Predefined Channels" / "My Channels" are vapor items
The top two items of the knob menu (`menu.predefined`, `menu.mine` —
`player.html:2182-2183`) have **no select handler**: choosing them hits
`// resto (predefinidos, etc.) todavía WIP` (`player.html:2461`) and just resets the menu
timer. This was already true at the upstream fork point (`aa6232a`, same WIP comment) and
upstream has no backend concept of predefined-vs-user channels either — they are menu
stubs for a feature that was never built, by anyone. Only "Settings", "Gaming", and "Back"
work in the main menu.

### 6.4 Write-only / dead config keys
| Key | Evidence |
|---|---|
| `tags_excluidos` (channel) | Written in `DEFAULT_CANALES` (`app.py:238`); read by nothing — even the legacy picker only uses `tags_incluidos`. |
| `intro_video_id` (channel) | Accepted by `guardar_canal` (`app.py:2738-2771`) but never read — true upstream as well (write-only there too). Per-channel intro videos were never implemented; the boot intro is the separate `INTRO_FLAG`/splash-rotation system. |
| `configuracion.json` globals (`tags_prioridad`/`tags_incluidos`) | Auto-populated at boot (`app.py:351`) with every known tag; only the legacy picker reads them; the editor UI is deleted (§2.2). |

### 6.5 i18n: a trilingual engine serving a mostly-monolingual UI — RESOLVED 2026-07
> **Status: fixed.** All admin pages now use `tr()` with complete `es/en/de` bundles
> (index, canales, series, upload_series, upload_commercials, edit, video, vcr_admin,
> vcr_record, vertele), JS strings translate via a `window.TR`/`window.tr` helper, and
> the gaming overlay uses `menu.gaming_entering`. Two additional remnants were found and
> removed during the fix: three views (`series_page`, `upload_series`,
> `upload_commercials`) defined a **local `tr()` shadow** that read only the base
> dictionary with nested-key semantics, silently overriding the real i18n system via
> `render_template(..., tr=tr)`; and `/video`//`/edit` crashed with `NameError` on a
> fresh server because the module-level `metadata` cache was never initialized (now
> seeded at startup). Still untranslated by design: server-side API error strings
> (console/log-only), and the dead pages `upload.html`/`kiosk_boot.html` (slated for
> removal). The original findings below are kept for history.

**Original findings (pre-fix):**
The i18n mechanism itself is complete and healthy: base dictionaries `es/en/de.json`
(~89 keys, near-perfect parity — `en.json` only lacks `wifi.ap_qr_title`), per-page
overrides merged server-side via the endpoint map (`app.py:4195`), `tr()` injected into
Jinja, a client fetch path (`/i18n/<lang>.json`), an OSD language switcher, and
persistence in `menu_configuracion.json` via `/api/language`.

But the fork's rewrites abandoned it on the admin side, splitting the app in two:

- **Genuinely trilingual:** the TV itself (`player.html`, 92 `tr()` calls — all OSD menus
  and overlays), `wifi_setup.html` (33/34 keys), and the `base.html` nav. One stray:
  the gaming overlay is hardcoded Spanish — `mostrarOverlay("Entrando en Modo Juegos…")`
  (`player.html:2451`) displays raw Spanish in every language.
- **Hardcoded English (fork-era pages, zero `tr()`):** `index.html` (uses 2 of its 42
  i18n keys), `series.html`, `upload_series.html`, `upload_commercials.html`,
  `vcr_admin.html`, `vcr_record.html`, `video.html`; `canales.html` is mixed (9/17 keys).
- **Hardcoded Spanish (upstream-era, zero `tr()`):** `edit.html` — "✏️ Editar video",
  "Guardar", "Vaciar" — so the otherwise-English admin UI drops into Spanish on the
  video editor regardless of the language setting.
- **Spanish-only server strings:** API error messages ignore the language system
  entirely (e.g. "No hay tags incluidos…", "Idioma no soportado").

Dead translation corpus: `tags_*.json` (29×3) and `configuracion_*.json` (18×3) target
deleted pages; `upload_*.json` (12×3) targets the orphan upload page; ~40 of 42 keys in
`index_*.json` are no longer referenced. In practice the language switcher only affects
the on-TV experience and the WiFi captive portal.

### 6.6 Upstream seed data
Fresh installs are seeded with upstream's Argentine content vocabulary and defaults:
`DEFAULT_TAGS` (`app.py:215` — Mirtha, Franchella, Menem, Cristina, Milei, "menemismo",
mate icon...) and `DEFAULT_CANALES` (`app.py:231` — "Canal de Prueba" with
`"icono": "mate.png"`, an image filename although the fork's channel UI uses emoji icons
and no such image exists; `vertele.html:132` would render the literal text "mate.png").

---

## 7. Not remnants — alive despite looking suspicious

Listed to prevent accidental deletion during cleanup:

- **Gaming / EmulationStation toggle** (`Config_files/servicios_y_scripts_toggle_tva_games/`):
  installed by `install.sh:226-402`, driven by `/api/gaming` (`app.py:3265-3300`) from the
  player OSD menu (`player.html:2185, 2450-2453`). Depends on an external RetroPie install —
  expected, not broken. (Its two splash-image script lines are broken though; see §1.3.)
- **VCR/NFC stack** (`vcr_manager.py`, `nfc_reader.py`, `vcr_record.html`): fully live;
  `nfc_reader.py` and `metadata_daemon.py` are launched via `subprocess.Popen` from
  `app.py`'s `__main__` block (`app.py:4324-4348`), so a plain import-grep falsely flags them.
- **`bluetooth_manager.py` / `wifi_manager.py`**: imported at `app.py:39-40`, backing
  `/api/bt/*` and `/api/wifi/*` plus `wifi_setup.html`.
- **`Config_files/audio_keepalive/`**: copied to `/etc/asound.conf` by `install.sh:521-538`
  (HiFiBerry dmix / audio-pop fix).
- **`player_utils.py`**: imported by `tvargenta_encoder.py:8`.
- **Tag data model** (`tags.json`, `configuracion.json` bootstrap in `app.py:343-367`):
  still feeds the scheduler and edit page — only the standalone *editor UIs* are gone (§2.1, §2.2).
- **`test_scheduler.py`**: live pytest suite.
