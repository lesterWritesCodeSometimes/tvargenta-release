# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta — Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# © 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribución y consulta previa. Se distribuye TAL CUAL, sin garantías.
# Ver LICENSE para términos completos.


from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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
CHANNEL_DETECTION_CACHE_FILE = CONTENT_DIR / "channel_detection_cache.json"
CANAL_ACTIVO_FILE   = CONTENT_DIR / "canal_activo.json"
CONFIG_PATH         = CONTENT_DIR / "menu_configuracion.json"
SERIES_FILE         = CONTENT_DIR / "series.json"
PLAYS_FILE          = SYSTEM_DATA_DIR / "content" / "plays.json"  # persiste fuera del repo si corres en /srv

# Series video directory
SERIES_VIDEO_DIR    = VIDEO_DIR / "series"

# Commercials video directory
COMMERCIALS_DIR     = VIDEO_DIR / "commercials"

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

# =============================================================================
# Timezone Configuration
# =============================================================================
# The system runs in UTC. This setting defines the app's display/scheduling timezone.
# All user-facing time operations should use app_now() instead of datetime.now().
#
# Timezone is configurable via:
#   1. On-screen menu (stored in configuracion.json)
#   2. Environment variable TVARGENTA_TIMEZONE (fallback)
#   3. Default: America/New_York
#
# Common timezone examples:
#   "America/New_York", "America/Los_Angeles", "America/Chicago"
#   "Europe/London", "Europe/Paris", "Europe/Madrid"
#   "Asia/Tokyo", "Australia/Sydney"
#   "UTC" for no conversion

# Default timezone (used if not configured elsewhere)
DEFAULT_TIMEZONE = "America/New_York"

# Cached ZoneInfo object and name
_app_timezone = None
_app_timezone_name = None

def _load_timezone_from_config() -> str:
    """Load timezone from configuracion.json, falling back to env var or default."""
    # Try to load from config file first
    try:
        if CONFIG_FILE.exists():
            import json
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                tz = cfg.get("timezone")
                if tz:
                    return tz
    except Exception as e:
        print(f"[SETTINGS] Could not load timezone from config: {e}")

    # Fall back to environment variable or default
    return os.environ.get("TVARGENTA_TIMEZONE", DEFAULT_TIMEZONE)

def get_app_timezone_name() -> str:
    """Get the name of the configured timezone."""
    global _app_timezone_name
    if _app_timezone_name is None:
        _app_timezone_name = _load_timezone_from_config()
    return _app_timezone_name

def get_app_timezone() -> ZoneInfo:
    """Get the configured app timezone as a ZoneInfo object."""
    global _app_timezone, _app_timezone_name

    # Check if we need to reload
    current_name = _load_timezone_from_config()
    if _app_timezone is None or _app_timezone_name != current_name:
        _app_timezone_name = current_name
        try:
            _app_timezone = ZoneInfo(_app_timezone_name)
        except Exception as e:
            print(f"[SETTINGS] Invalid timezone '{_app_timezone_name}', falling back to UTC: {e}")
            _app_timezone = ZoneInfo("UTC")
            _app_timezone_name = "UTC"
    return _app_timezone

def reload_timezone() -> None:
    """Force reload of timezone from config. Call after changing timezone setting."""
    global _app_timezone, _app_timezone_name
    _app_timezone = None
    _app_timezone_name = None
    get_app_timezone()  # Trigger reload

def app_now() -> datetime:
    """
    Get the current time in the app's configured timezone.

    Use this instead of datetime.now() for all user-facing time operations
    (scheduling, display, etc.). The returned datetime is timezone-aware.

    Returns:
        datetime: Current time in the app's configured timezone (timezone-aware)
    """
    return datetime.now(get_app_timezone())

def to_app_timezone(dt: datetime) -> datetime:
    """
    Convert a datetime to the app's configured timezone.

    Args:
        dt: A datetime object (naive datetimes are assumed to be UTC)

    Returns:
        datetime: The same instant in the app's configured timezone
    """
    if dt.tzinfo is None:
        # Assume naive datetimes are UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_app_timezone())
