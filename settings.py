# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta — Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# © 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribución y consulta previa. Se distribuye TAL CUAL, sin garantías.
# Ver LICENSE para términos completos.


from pathlib import Path
import os, getpass


ENV_ROOT = os.environ.get("TVARGENTA_ROOT")
if ENV_ROOT:
    ROOT_DIR = Path(ENV_ROOT).resolve()
else:
    # .../software/app -> .../software -> .../ (repo root)
    ROOT_DIR = Path(__file__).resolve().parents[0]

APP_DIR     = ROOT_DIR 
CONTENT_DIR = ROOT_DIR / "content"
VIDEO_DIR   = CONTENT_DIR / "videos"
THUMB_DIR   = CONTENT_DIR / "thumbnails"
TEMPLATES_DIR   = ROOT_DIR / "templates"
LOG_DIR = ROOT_DIR / "logs"

LOG_DIR.mkdir(parents=True, exist_ok=True)

# Archivos de estado (en /tmp por defecto)
TMP_DIR = Path("/tmp")

# Splash y perfil de Chromium: si existe /srv/tvargenta, usamos como “datos del sistema”
# Si no, usamos ROOT_DIR.
SYSTEM_DATA_DIR = Path("/srv/tvargenta") if Path("/srv/tvargenta").exists() else ROOT_DIR
SPLASH_DIR      = APP_DIR / "Splash" / "videos"
I18N_DIR        = TEMPLATES_DIR / "i18n"
CHROME_PROFILE  = SYSTEM_DATA_DIR / ".chromium-profile"
CHROME_CACHE    = SYSTEM_DATA_DIR / ".chromium-cache"

# Archivos JSON
METADATA_FILE       = CONTENT_DIR / "metadata.json"
TAGS_FILE           = CONTENT_DIR / "tags.json"
CONFIG_FILE         = CONTENT_DIR / "configuracion.json"
CANALES_FILE        = CONTENT_DIR / "canales.json"
CANAL_ACTIVO_FILE   = CONTENT_DIR / "canal_activo.json"
CONFIG_PATH         = CONTENT_DIR / "menu_configuracion.json"
SERIES_FILE         = CONTENT_DIR / "series.json"
PLAYS_FILE          = SYSTEM_DATA_DIR / "content" / "plays.json"  # persiste fuera del repo si corres en /srv

# Series video directory
SERIES_VIDEO_DIR    = VIDEO_DIR / "series"

SPLASH_STATE_FILE   = SYSTEM_DATA_DIR / "Splash" / "splash_state.json"
INTRO_PATH          = SPLASH_DIR / "splash_1.mp4"

# Usuario que corre el kiosk 
USER = os.environ.get("TVARGENTA_USER") or getpass.getuser()

UPLOAD_STATUS = TMP_DIR / "upload_status.txt"

# VCR / NFC paths
VCR_STATE_FILE = TMP_DIR / "vcr_state.json"
VCR_TRIGGER_FILE = TMP_DIR / "trigger_vcr.json"
VCR_PAUSE_TRIGGER = TMP_DIR / "trigger_vcr_pause.json"
VCR_REWIND_TRIGGER = TMP_DIR / "trigger_vcr_rewind.json"
VCR_COUNTDOWN_TRIGGER = TMP_DIR / "trigger_vcr_countdown.json"
VCR_RECORDING_STATE_FILE = TMP_DIR / "vcr_recording_state.json"
TAPES_FILE = CONTENT_DIR / "tapes.json"

# Broadcast scheduling paths
SCHEDULE_FILE = SYSTEM_DATA_DIR / "schedule.json"
DAILY_SCHEDULE_FILE = SYSTEM_DATA_DIR / "daily_schedule.json"
TEST_PATTERN_FILE = VIDEO_DIR / "test_pattern.png"
HANG_TIGHT_FILE = VIDEO_DIR / "hang_tight.png"
ADS_DIR = VIDEO_DIR / "ads"

# Time slot definitions for broadcast scheduling (hour ranges)
TIME_SLOTS = {
    "early_morning": (4, 7),    # 4:00 AM - 7:00 AM
    "late_morning": (7, 12),    # 7:00 AM - 12:00 PM
    "afternoon": (12, 17),      # 12:00 PM - 5:00 PM
    "evening": (17, 21),        # 5:00 PM - 9:00 PM
    "night": (21, 28),          # 9:00 PM - 4:00 AM (28 = 4 AM next day)
}

# Valid time_of_day options for series metadata
VALID_TIME_OF_DAY = ["early_morning", "late_morning", "afternoon", "evening", "night", "any"]
