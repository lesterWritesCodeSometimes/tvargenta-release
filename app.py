# SPDX-License-Identifier: LicenseRef-TVArgenta-NC-Attribution-Consult-First
# Proyecto: TVArgenta â€” Retro TV
# Autor: Ricardo Sappia contact:rsflightronics@gmail.com
# Â© 2025 Ricardo Sappia. Todos los derechos reservados.
# Licencia: No comercial, atribuciÃ³n y consulta previa. Se distribuye TAL CUAL, sin garantÃ­as.
# Ver LICENSE para tÃ©rminos completos.


from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory, flash, render_template_string, send_file, g
import threading
import os
import json
import subprocess
from werkzeug.utils import secure_filename
import tempfile
import shutil
from datetime import datetime, UTC
import atexit
import signal
import time
import random
import logging
from logging.handlers import RotatingFileHandler
import math
import base64, urllib.parse
import socket
from pathlib import Path
from settings import (
    ROOT_DIR, APP_DIR, CONTENT_DIR, VIDEO_DIR, THUMB_DIR,
    METADATA_FILE, TAGS_FILE, CONFIG_FILE, CANALES_FILE, CANAL_ACTIVO_FILE,
    SPLASH_DIR, SPLASH_STATE_FILE, INTRO_PATH, CHROME_PROFILE, CHROME_CACHE,
    PLAYS_FILE, USER, UPLOAD_STATUS, TMP_DIR, CONFIG_PATH, LOG_DIR, I18N_DIR,
    VCR_STATE_FILE, VCR_TRIGGER_FILE, TAPES_FILE, VCR_RECORDING_STATE_FILE,
    SERIES_FILE, SERIES_VIDEO_DIR, COMMERCIALS_DIR,
)
import re
import bluetooth_manager
import wifi_manager
import vcr_manager
import scheduler


       

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)  # solo errores visibles
app = Flask(__name__)

# --- LOGGING ---------------------------------------------------------------
LOG_PATH = str(LOG_DIR  / "tvargenta.log") 
logger = logging.getLogger("tvargenta")
logger.setLevel(logging.INFO)

if not logger.handlers:
    _h = RotatingFileHandler(LOG_PATH, maxBytes=3_000_000, backupCount=5)
    _fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    _h.setFormatter(_fmt)
    logger.addHandler(_h)
    
    
    # además: mandar a stdout -> systemd/journald
    sh = logging.StreamHandler()
    sh.setFormatter(_fmt)
    logger.addHandler(sh)

def _hdr(name):
    # mini helper para loggear origen de las requests
    ref = request.headers.get("Referer", "-")
    ua  = request.headers.get("User-Agent", "-")
    ip  = request.headers.get("X-Forwarded-For") or request.remote_addr
    return f"{name} ref={ref} ip={ip} ua={ua}"
# --------------------------------------------------------------------------



# Ruta base de videos y metadatos
TRIGGER_PATH = str(TMP_DIR / "trigger_reload.json")
VOLUMEN_PATH = str(TMP_DIR / "tvargenta_volumen.json")
MENU_TRIGGER_PATH = str(TMP_DIR / "trigger_menu.json")
MENU_STATE_PATH  = str(TMP_DIR / "menu_state.json")
MENU_NAV_PATH    = str(TMP_DIR / "trigger_menu_nav.json")
MENU_SELECT_PATH = str(TMP_DIR / "trigger_menu_select.json")

INTRO_FLAG  = "/tmp/tvargenta_show_intro"
LAUNCH_FLAG      = str(TMP_DIR / "tvargenta_kiosk_launched")
CURRENT_SPLASH_FILE = str(TMP_DIR / "tvargenta_current_splash.json")


ALSA_DEVICE   = "default"   
SPLASH_DONE   = "/tmp/.tvargenta_splash_done"

PING_FILE = "/tmp/tvargenta_kiosk_ping.txt"
FRONT_PING_PATH = "/tmp/tvargenta_front_ping.json"

CONTENT_DIR = Path(CONTENT_DIR)

DEFAULT_CONFIG = {"tags_prioridad": [], "tags_incluidos": []}
DEFAULT_CANAL_ACTIVO = {"canal_id": "1"}


_ap_auto_stop_timer = None
_AP_AUTO_STOP_SECONDS = 120  # ajustar si querés (2 minutos por defecto)

# Asegurar que no quede modo AP activo al iniciar Flask
try:
    wifi_manager.cleanup_ap_if_stale(max_age_seconds=180)
except Exception as e:
    logger.warning(f"[WiFi] cleanup_ap_if_stale on app startup failed: {e}")      

def _start_ap_auto_stop_timer():
    global _ap_auto_stop_timer
    # cancelar si había uno previo
    try:
        if _ap_auto_stop_timer and _ap_auto_stop_timer.is_alive():
            _ap_auto_stop_timer.cancel()
    except Exception:
        pass

    def _stop_if_still_ap():
        try:
            st = wifi_manager.get_status()
            logger.info(f"[WiFi][Timer] AP auto-stop check -> {st}")
            # si sigue en modo ap, forzamos stop
            if st.get("mode") == "ap":
                wifi_manager.stop_ap_mode()
                logger.info("[WiFi][Timer] stop_ap_mode() ejecutado por timer")
        except Exception as e:
            logger.warning(f"[WiFi][Timer] Error during auto-stop: {e}")

    _ap_auto_stop_timer = threading.Timer(_AP_AUTO_STOP_SECONDS, _stop_if_still_ap)
    _ap_auto_stop_timer.daemon = True
    _ap_auto_stop_timer.start()


DEFAULT_VOL = 25  # % volumen por defecto

def init_volumen_por_defecto():
    """Si no existe el archivo de volumen, lo crea con DEFAULT_VOL y notifica al front."""
    try:
        if not os.path.exists(VOLUMEN_PATH):
            with open(VOLUMEN_PATH, "w") as f:
                json.dump({"valor": DEFAULT_VOL}, f)
            # avisar al front para que levante el valor inicial
            with open("/tmp/trigger_volumen.json", "w") as f:
                json.dump({"timestamp": time.time()}, f)
            logger.info(f"[VOLUMEN] Default inicializado en {DEFAULT_VOL}%")
    except Exception as e:
        logger.warning(f"[VOLUMEN] No pude inicializar default: {e}")



def get_next_splash_path():
    """
    Elige el splash *para este arranque* sin avanzar todavÃ­a el Ã­ndice persistente.
    Escribe la elecciÃ³n en /tmp para que /splash la use.
    """
    try:
        files = sorted(
            f for f in os.listdir(SPLASH_DIR)
            if f.lower().endswith(".mp4") and f.startswith("splash_")
        )
    except Exception as e:
        logger.error(f"[SPLASH] no puedo listar {SPLASH_DIR}: {e}")
        files = []

    if not files:
        # fallback al que ya usabas
        path = INTRO_PATH if os.path.isfile(INTRO_PATH) else None
        logger.info(f"[SPLASH] fallback path={path}")
    else:
        st = _load_splash_state()
        idx = st.get("index", 0) % len(files)
        path = os.path.join(SPLASH_DIR, files[idx])
        logger.info(f"[SPLASH] choose idx={idx} file={files[idx]}")

    # guardo la selecciÃ³n de este run
    try:
        with open(CURRENT_SPLASH_FILE, "w", encoding="utf-8") as f:
            json.dump({"path": path}, f)
    except Exception as e:
        logger.warning(f"[SPLASH] no pude escribir CURRENT_SPLASH_FILE: {e}")

    return path

try:
    with open(INTRO_FLAG, "w") as f:
        f.write("1")
except Exception:
    pass
    
try:
    _maybe = get_next_splash_path()
    if _maybe and os.path.isfile(_maybe):
        INTRO_PATH = _maybe
except Exception:
    pass

os.makedirs(os.path.dirname(PLAYS_FILE), exist_ok=True)

# Tags y grupos por defecto
DEFAULT_TAGS = {
    "Personajes": {
        "color": "#facc15",
        "tags": ["Mirtha", "Franchella", "Menem", "Cristina", "Milei"]
    },
    "Temas": {
        "color": "#3b82f6",
        "tags": ["politica", "humor", "clasicos", "virales", "efemerides", "publicidad"]
    },
    "Otros": {
        "color": "#ec4899",
        "tags": ["Simuladores", "Simpsons", "familia", "personal", "milagros", "menemismo", "test"]
    }
}

# Canales predefinidos (Channel 03 is a system channel, not stored here)
DEFAULT_CANALES = {
    "Canal de Prueba": {
        "nombre": "Test",
        "descripcion": "Canal de prueba",
        "tags_prioridad": ["test"],
        "tags_excluidos": [],
        "icono": "mate.png",
        "intro_video_id": ""
    }
}

shown_videos_por_canal = {}

# --- Anti-bounce / cooldown ---
last_next_call = {}   # canal_id -> timestamp del Ãºltimo /api/next_video servido
NEXT_COOLDOWN = 0.5   # segundos de ventana anti-encadenados (reduced for responsiveness)
STICKY_WINDOW = 1.0   # segundos (reduced for responsiveness)
last_choice_per_canal = {}  # canal_id -> {"video_id": str, "ts": float}

# --- De-dupe primer NEXT por canal ---
pending_pick = {}  # canal_id -> {"video_id": str, "ts": float}
PENDING_TTL = 12.0  # segundos; reusar el mismo pick dentro de este tiempo

_last_trigger_mtime_served = 0.0  # para /api/should_reload (one-shot)
_last_menu_mtime_served = 0.0
_last_nav_mtime_served = 0.0
_last_sel_mtime_served = 0.0
# --- Para distinguir el origen del trigger (p. ej. BTN_NEXT) ---
_last_trigger_reason = ""
_last_trigger_mtime  = 0.0
_force_next_once     = False  # si True, el próximo /api/next_video ignora sticky/cooldown

# --- Boot / Frontend probes -------------------------------------------------
_last_frontend_ping = 0.0  # epoch de Ãºltimo ping recibido
_last_frontend_stage = "boot"
PING_GRACE = 25.0  # segundos de gracia despuÃ©s de lanzar Chromium
_watchdog_already_retry = False # evita relanzar Chromium mÃ¡s de 1 vez

if os.path.exists(TRIGGER_PATH):
    try:
        _last_trigger_mtime_served = os.path.getmtime(TRIGGER_PATH)
    except Exception:
        _last_trigger_mtime_served = 0.0
        
def _write_json_atomic(path, data):
    path = Path(path)  # acepta str o Path
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(path.suffix + ".tmp")  # p.ej. plays.json.tmp
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # atÃ³mico en el mismo fs

def _ensure_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _write_json_atomic(path, data)
        
def _all_tags_from_tagsfile():
    """Devuelve el set de todos los tags definidos en tags.json."""
    try:
        tags_data = load_tags()  # ya la tenÃ©s definida mÃ¡s abajo
        return {t for grupo in tags_data.values() for t in grupo.get("tags", [])}
    except Exception:
        return set()
        
def _bootstrap_config_from_tags_if_empty():
    """
    Si configuracion.json no tiene 'tags_incluidos', lo poblamos con TODOS los tags
    de tags.json. Y si 'tags_prioridad' estÃ¡ vacÃ­o, lo iniciamos con el mismo orden.
    """
    try:
        cfg = load_config()  # <- esta funciÃ³n ya debe estar definida al momento de llamar
        if not cfg.get("tags_incluidos"):
            todos = sorted(_all_tags_from_tagsfile())
            if todos:
                cfg["tags_incluidos"] = todos
                if not cfg.get("tags_prioridad"):
                    cfg["tags_prioridad"] = todos[:]
                _write_json_atomic(CONFIG_FILE, cfg)  # ATÃ“MICO
                logger.info(f"[BOOT] Config inicial poblada con {len(todos)} tags desde tags.json")
    except Exception as e:
        logger.warning(f"[BOOT] No pude poblar configuracion desde tags.json: {e}")

# Semillas de JSONs (no pisan si ya existen)
_ensure_json(TAGS_FILE,       DEFAULT_TAGS)
_ensure_json(CONFIG_FILE,     DEFAULT_CONFIG)
_ensure_json(CANALES_FILE,    DEFAULT_CANALES)
_ensure_json(METADATA_FILE,   {})
_ensure_json(CANAL_ACTIVO_FILE, DEFAULT_CANAL_ACTIVO)
_ensure_json(SERIES_FILE,     {})

# Ensure series video directory exists
SERIES_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

# Note: Channel 03 is a system channel (AV input) - it's injected at runtime
# by the encoder and handled specially by /api/next_video. It's not stored in canales.json.

# ============================================================================
# SERIES HELPER FUNCTIONS
# ============================================================================

def series_display_name(folder_name):
    """Convert folder name to display name: Los_Simuladores → Los Simuladores"""
    return (folder_name or "").replace('_', ' ')

def series_folder_name(display_name):
    """Convert display name to folder name: Los Simuladores → Los_Simuladores"""
    # Also sanitize: remove any characters that aren't alphanumeric, underscore, or hyphen
    name = (display_name or "").replace(' ', '_')
    return re.sub(r'[^\w\-]', '', name)

def parse_episode_info(filename):
    """
    Parse season/episode from filename. Returns (season, episode) or (None, None).
    Supports: S01E05, s1e5, 1x05, Season 1 Episode 5, Season1Episode5
    """
    patterns = [
        r'[Ss](\d+)[Ee](\d+)',                      # S01E05, s1e5
        r'(\d+)[xX](\d+)',                           # 1x05
        r'[Ss]eason\s*(\d+)\s*[Ee]pisode\s*(\d+)',  # Season 1 Episode 5, Season1Episode5
    ]
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None, None

def load_series():
    """Load series data from series.json"""
    try:
        if SERIES_FILE.exists():
            with open(SERIES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"[SERIES] Error loading series.json: {e}")
    return {}

def save_series(data):
    """Save series data to series.json"""
    _write_json_atomic(SERIES_FILE, data)

def scan_series_directories():
    """
    Scan series directories on startup:
    1. Find all series folders in content/videos/series/
    2. Add new series to series.json
    3. Scan for video files and create/update metadata
    """
    if not SERIES_VIDEO_DIR.exists():
        SERIES_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        return

    series_data = load_series()
    metadata = load_metadata()
    changes_made = False

    # Scan for series directories
    for series_dir in SERIES_VIDEO_DIR.iterdir():
        if not series_dir.is_dir():
            continue

        series_name = series_dir.name

        # Add to series.json if not present
        if series_name not in series_data:
            series_data[series_name] = {
                "created": datetime.now().strftime("%Y-%m-%d")
            }
            logger.info(f"[SERIES] Discovered new series: {series_display_name(series_name)}")
            changes_made = True

        # Scan for video files in this series
        for video_file in series_dir.glob("*.mp4"):
            video_id = video_file.stem  # filename without extension
            series_path = f"series/{series_name}/{video_id}"

            # Check if we already have metadata for this video
            existing = metadata.get(video_id, {})

            # Parse season/episode from filename
            season, episode = parse_episode_info(video_id)

            # Create or update metadata
            if video_id not in metadata or existing.get("series_path") != series_path:
                metadata[video_id] = {
                    "title": existing.get("title") or video_id,
                    "category": "tv_episode",
                    "series": series_name,
                    "series_path": series_path,
                    "season": existing.get("season") or season,
                    "episode": existing.get("episode") or episode,
                    "tags": existing.get("tags", []),
                    "personaje": existing.get("personaje", ""),
                    "fecha": existing.get("fecha", ""),
                    "modo": existing.get("modo", []),
                    "duracion": existing.get("duracion")
                }
                logger.info(f"[SERIES] Added/updated metadata for {series_path}")
                changes_made = True

            # Generate thumbnail if missing
            thumb_path = THUMB_DIR / f"{video_id}.jpg"
            if not thumb_path.exists():
                try:
                    generate_thumbnail(str(video_file), str(thumb_path))
                    logger.info(f"[SERIES] Generated thumbnail for {video_id}")
                except Exception as e:
                    logger.warning(f"[SERIES] Failed to generate thumbnail for {video_id}: {e}")

    # Save changes
    if changes_made:
        save_series(series_data)
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        logger.info("[SERIES] Saved series and metadata updates")

def generate_thumbnail(video_path, thumb_path):
    """Generate a thumbnail from a video file using ffmpeg"""
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-ss", "00:00:02",
        "-vframes", "1",
        "-vf", "scale=320:-1",
        thumb_path
    ], check=True, capture_output=True, timeout=30)  


def load_config_i18n():
    """
    Lee menu_configuracion.json y garantiza:
    - Que exista el archivo.
    - Que tenga la clave 'language'.
    - Que los logs indiquen qué se está cargando.
    """
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
            if "language" not in cfg:
                cfg["language"] = "es"
                save_config_i18n(cfg)
                logger.warning(
                    f"[I18N] Falta 'language' en {CONFIG_PATH}, agregado 'es'."
                )
            #logger.info(
            #    f"[I18N] Cargado config de {CONFIG_PATH} -> language={cfg.get('language')!r}"
            #)
            return cfg
        else:
            cfg = {"language": "es"}
            save_config_i18n(cfg)
            logger.warning(f"[I18N] {CONFIG_PATH} no existía. Creado con language='es'.")
            return cfg
    except Exception as e:
        logger.error(f"[I18N] Error leyendo {CONFIG_PATH}: {e}")
        return {"language": "es"}


def save_config_i18n(cfg):
    """
    Persiste menu_configuracion.json y deja trazas claras en el journal.
    """
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        logger.info(
            f"[I18N] Guardado OK -> {CONFIG_PATH} | language={cfg.get('language')} "
            f"| claves={list(cfg.keys())}"
        )
    except Exception as e:
        logger.error(f"[I18N] Error guardando {CONFIG_PATH}: {e}")

def load_translations(lang):
    path = I18N_DIR / f"{lang}.json"
    if not path.exists():
        logger.warning(f"[I18N] Base {lang}.json no existe, usando es.json")
        path = I18N_DIR / "es.json"

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f) or {}
        #logger.info(f"[I18N] Base {path.name} cargada con {len(data)} claves")
        return data
    except Exception as e:
        logger.error(f"[I18N] Error leyendo {path}: {e}")
        return {}

        
def load_page_translations(lang: str, page: str) -> dict:
    """
    Carga traducciones específicas de una página, p.ej. index_es.json.
    """
    page_file = I18N_DIR / f"{page}_{lang}.json"
    if not page_file.exists():
        logger.info(f"[I18N] No existe i18n de página: {page_file.name}")
        return {}

    try:
        with page_file.open("r", encoding="utf-8") as f:
            data = json.load(f) or {}
        logger.info(f"[I18N] Página {page_file.name} cargada con {len(data)} claves")
        return data
    except Exception as e:
        logger.error(f"[I18N] Error leyendo {page_file}: {e}")
        return {}

 

def restart_kiosk(url="http://localhost:5000/tv"):
    env = os.environ.copy()
    env["DISPLAY"] = ":0"
    env["XAUTHORITY"] = f"/home/{USER}/.Xauthority"

    chromium_log = "/tmp/chromium_kiosk.log"

    try:
        # Cerrar sÃ³lo lo visible del browser
        subprocess.run(["pkill", "-f", "chromium"], check=False)

        # Esperar X (:0) hasta ~12s
        for i in range(60):
            if os.path.exists("/tmp/.X11-unix/X0"):
                break
            time.sleep(0.2)
        else:
            logger.error("[KIOSK] DISPLAY :0 no disponible. Aborto lanzamiento.")
            return

        # Esperar que Flask estÃ© sirviendo la URL raÃ­z (o /) antes de lanzar
        import urllib.request
        for _ in range(30):
            try:
                urllib.request.urlopen("http://127.0.0.1:5000/", timeout=1)
                break
            except:
                time.sleep(0.3)

        user_data_dir = str(CHROME_PROFILE)
        cache_dir     = str(CHROME_CACHE)
        for d in (user_data_dir, cache_dir):
            try:
                os.makedirs(d, exist_ok=True)
                os.chmod(d, 0o755)
            except Exception as e:
                logger.warning(f"[KIOSK] No pude preparar {d}: {e}")

        chromium_bin = "/usr/bin/chromium-browser" if os.path.exists("/usr/bin/chromium-browser") else "/usr/bin/chromium"
        
        # Limpieza de locks del perfil (cuando hay apagados bruscos quedan "Singleton*" y bloquea primer boot)
        try:
            for fn in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                p = os.path.join(user_data_dir, fn)
                if os.path.exists(p):
                    os.remove(p)
        except Exception as e:
            print("[KIOSK] No pude limpiar locks de perfil:", e)

        cmd = [
            chromium_bin,
            "--kiosk", f"--app={url}",
            "--noerrdialogs",
            "--disable-infobars",
            "--disable-translate",
            "--disable-features=Translate",
            "--autoplay-policy=no-user-gesture-required",
            "--ozone-platform=x11",
            "--use-gl=angle", "--use-angle=gl",
            "--disable-features=VaapiVideoDecoder",
            f"--user-data-dir={user_data_dir}",
            f"--disk-cache-dir={cache_dir}",
            # logging de Chromium para primer boot:
            "--enable-logging=stderr", 
            "--no-first-run",
            "--no-default-browser-check",
            "--log-level=2",
            
        ]
        logger.info(f"[KIOSK] Lanzando: {' '.join(cmd)} DISPLAY={env.get('DISPLAY')} X0={'ok' if os.path.exists('/tmp/.X11-unix/X0') else 'NO'} bin={chromium_bin}")

        # Redirigimos stdout/stderr a archivo para diagnÃ³sticos post-boot
        with open(chromium_log, "ab", buffering=0) as logf:
            subprocess.Popen(cmd, env=env, stdout=logf, stderr=logf)
            logger.info(f"[KIOSK] Chromium log -> {chromium_log}")

    except Exception as e:
        logger.error(f"[KIOSK] Error lanzando Chromium: {e}")

    
def launch_kiosk_once():
    try:
        if not os.path.exists(LAUNCH_FLAG):
            # si existe intro flag, arrancamos mostrando la imagen de espera (pre-loader local)
            if os.path.exists(INTRO_FLAG):
                url = Path(APP_DIR, "templates", "kiosk_boot.html").as_uri()
            else:
                url = "http://localhost:5000/"
            restart_kiosk(url=url)
            with open(LAUNCH_FLAG, "w") as f:
                f.write("1")
    except Exception as e:
        print("[KIOSK] Error lanzando Chromium:", e)


   
# FunciÃ³n para cargar canales
def load_canales():
    if os.path.exists(CANALES_FILE):
        with open(CANALES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

# FunciÃ³n para guardar canales
def save_canales(data):
    with open(CANALES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"tags_prioridad": [], "tags_incluidos": []}
    
# Ruta para ver y gestionar tags
def load_tags():
    with open(TAGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_tags(tags_data):
    with open(TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(tags_data, f, indent=2, ensure_ascii=False)

_bootstrap_config_from_tags_if_empty()

def get_canal_activo():
    if os.path.exists(CANAL_ACTIVO_FILE):
        with open(CANAL_ACTIVO_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("canal_id", "base")
    return "base"

def set_canal_activo(canal_id):
    with open(CANAL_ACTIVO_FILE, "w", encoding="utf-8") as f:
        json.dump({"canal_id": canal_id}, f, indent=2, ensure_ascii=False)


def get_canal_numero(canal_id, canales=None):
    """
    Get the display channel number for a given canal_id.
    - Channel "03" is always "03" (system AV input channel)
    - User channels can specify a custom "numero" field
    - Otherwise auto-assigned starting from "04" based on position
    """
    if canal_id == "03":
        return "03"

    if canales is None:
        canales = load_canales()

    # Check if channel has explicit numero
    canal_config = canales.get(canal_id, {})
    if canal_config.get("numero"):
        return str(canal_config["numero"]).zfill(2)

    # Auto-assign based on position (starting from 04, since 03 is system)
    canal_ids = list(canales.keys())
    try:
        idx = canal_ids.index(canal_id)
        return str(idx + 4).zfill(2)  # 04, 05, 06, ...
    except ValueError:
        return "04"  # fallback


def sanity_check_thumbnails(video_id=None):
    targets = [video_id] if video_id else metadata.keys()

    for vid in targets:
        # Check series_path first for series videos
        vid_info = metadata.get(vid, {})
        series_path = vid_info.get("series_path")
        if series_path:
            video_path = str(VIDEO_DIR / f"{series_path}.mp4")
        else:
            video_path = os.path.join(VIDEO_DIR, vid + ".mp4")

        thumbnail_path = os.path.join(CONTENT_DIR, "thumbnails", vid + ".jpg")

        if os.path.exists(video_path) and not os.path.exists(thumbnail_path):
            try:
                print(f"ðŸ–¼ Generando thumbnail para: {vid}")
                subprocess.run([
                    "ffmpeg",
                    "-ss", "00:00:02",
                    "-i", video_path,
                    "-frames:v", "1",
                    "-q:v", "4",
                    thumbnail_path
                ], check=True)
                print(f"âœ… Thumbnail generado: {thumbnail_path}")
            except Exception as e:
                print(f"âš ï¸ No se pudo generar thumbnail para {vid}. Se usarÃ¡ el por defecto. Error: {e}")

def get_video_resolution(filepath):
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filepath
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        width, height = map(int, result.stdout.strip().split("\n"))
        return width, height
    except Exception as e:
        print(f"âš ï¸ Error al obtener resoluciÃ³n de {filepath}: {e}")
        return None, None

def escribir_estado(status):
    with open(UPLOAD_STATUS, "w", encoding="utf-8") as f:
        f.write(status)

def eliminar_estado():
    if UPLOAD_STATUS.exists(): UPLOAD_STATUS.unlink()

def sincronizar_videos():
    # Scan regular videos in VIDEO_DIR
    archivos_video = {
        os.path.splitext(f)[0] for f in os.listdir(VIDEO_DIR)
        if f.lower().endswith(('.mp4', '.webm', '.mov')) and os.path.isfile(os.path.join(VIDEO_DIR, f))
    }

    # Scan series videos in SERIES_VIDEO_DIR/<series_name>/
    if SERIES_VIDEO_DIR.exists():
        for series_dir in SERIES_VIDEO_DIR.iterdir():
            if series_dir.is_dir():
                for f in series_dir.iterdir():
                    if f.is_file() and f.suffix.lower() in ('.mp4', '.webm', '.mov'):
                        archivos_video.add(f.stem)

    # Scan commercials in COMMERCIALS_DIR
    if COMMERCIALS_DIR.exists():
        for f in COMMERCIALS_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in ('.mp4', '.webm', '.mov'):
                archivos_video.add(f.stem)

    entradas_metadata = set(metadata.keys())

    # For series/commercial videos, check they exist at their path location
    def video_exists(video_id, data):
        series_path = data.get("series_path")
        commercials_path = data.get("commercials_path")
        if series_path:
            # Series video - check in series directory
            full_path = VIDEO_DIR / f"{series_path}.mp4"
            return full_path.exists()
        elif commercials_path:
            # Commercial video - check in commercials directory
            full_path = VIDEO_DIR / f"{commercials_path}.mp4"
            return full_path.exists()
        else:
            # Regular video - check in VIDEO_DIR
            return video_id in archivos_video

    videos_validos = {k: v for k, v in metadata.items() if video_exists(k, v)}
    videos_fantasmas = {k: v for k, v in metadata.items() if not video_exists(k, v)}
    videos_nuevos = sorted(archivos_video - entradas_metadata)

    return videos_validos, videos_fantasmas, videos_nuevos


def backup_tags():
    if os.path.exists(TAGS_FILE):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(CONTENT_DIR, f"tags_backup_{timestamp}.json")
        shutil.copy(TAGS_FILE, backup_file)
        print(f"ðŸ§¾ Backup creado: {backup_file}")


def clean_config_tags(tags_data, config_data):
    # Lista completa de tags vÃ¡lidos (los que existen actualmente)
    valid_tags = {tag for info in tags_data.values() for tag in info["tags"]}

    # Filtrar configuraciÃ³n y eliminar fantasmas
    config_data["tags_prioridad"] = [t for t in config_data.get("tags_prioridad", []) if t in valid_tags]
    config_data["tags_incluidos"] = [t for t in config_data.get("tags_incluidos", []) if t in valid_tags]

    return config_data

def get_video_duration(filepath):
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filepath
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"âš ï¸ No se pudo obtener duraciÃ³n de {filepath}: {e}")
        return 0


def verify_h264_codec(filepath):
    """
    Verify that a video file uses H.264 codec.
    Returns (is_valid, error_message).
    """
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filepath
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)

        codec = result.stdout.strip().lower()
        if codec in ("h264", "avc"):
            return True, None
        elif codec:
            return False, f"Unsupported codec: {codec}. Only H.264 is allowed."
        else:
            return False, "Could not detect video codec."
    except subprocess.TimeoutExpired:
        return False, "Codec verification timed out."
    except Exception as e:
        return False, f"Codec verification failed: {str(e)}"


def ensure_durations():
    updated = False
    for video_id, info in metadata.items():
        if "duracion" not in info:
            # Check series_path first, then fall back to VIDEO_DIR
            series_path = info.get("series_path")
            if series_path:
                filepath = VIDEO_DIR / f"{series_path}.mp4"
            else:
                filepath = os.path.join(VIDEO_DIR, f"{video_id}.mp4")
            if os.path.exists(filepath):
                dur = get_video_duration(str(filepath))
                metadata[video_id]["duracion"] = dur
                updated = True
    if updated:
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        print("ðŸ•' Duraciones actualizadas en metadata")

def get_total_recuerdos():
    total_sec = sum(v.get("duracion", 0) for v in metadata.values())
    horas = int(total_sec // 3600)
    minutos = int((total_sec % 3600) // 60)

    if horas:
        return f"{horas}h {minutos}m"
    else:
        return f"{minutos}m"
        
# --- Preferencias UI ---
def load_ui_prefs():
    cfg = load_config()
    # default: mostrar el nombre del canal
    return {"show_channel_name": bool(cfg.get("show_channel_name", True))}

def save_ui_prefs(prefs):
    cfg = load_config()
    cfg["show_channel_name"] = bool(prefs.get("show_channel_name", True))
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        


def _read_json(path, default):
    path = Path(path)  # acepta str o Path
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        # si estÃ¡ corrupto, devolvÃ© default
        return default



def load_plays():
    return _read_json(PLAYS_FILE, {})

def save_plays(d):
    _write_json_atomic(PLAYS_FILE, d)

def bump_play(video_id):
    d = load_plays()
    item = d.get(video_id, {"plays": 0, "last_played": None})
    item["plays"] = int(item.get("plays", 0)) + 1
    item["last_played"] = datetime.now(UTC).isoformat()
    d[video_id] = item
    save_plays(d)
    return item

def load_metadata():
    return _read_json(METADATA_FILE, {})

# Scan series directories on startup (must be after load_metadata is defined)
try:
    scan_series_directories()
except Exception as e:
    logger.error(f"[SERIES] Error during startup scan: {e}")

def _iso_to_ts(iso_str):
    try:
        return datetime.fromisoformat(iso_str.replace("Z","")).timestamp()
    except Exception:
        return 0.0

def score_for_video(video_id, metadata, plays_map):
    md = metadata.get(video_id, {})
    dur = float(md.get("duracion", 0.0))  # segundos
    minutes = max(1, math.ceil(dur / 60.0))

    pinfo = plays_map.get(video_id, {"plays": 0, "last_played": None})
    plays = int(pinfo.get("plays", 0))
    last_ts = _iso_to_ts(pinfo.get("last_played")) if pinfo.get("last_played") else 0.0

    plays_norm = plays / minutes
    # jitter muy pequeÃ±o para no ser determinista total
    jitter = random.random() * 0.01

    # Orden principal: menor plays_norm primero (mÃ¡s justo),
    # luego menos reciente (last_ts chico), luego jitter
    return (plays_norm, last_ts, jitter)

def _load_splash_state():
    """Lee /srv/tvargenta/Splash/splash_state.json -> {"index": int}"""
    try:
        if os.path.exists(SPLASH_STATE_FILE):
            with open(SPLASH_STATE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                idx = int(d.get("index", 0))
                logger.info(f"[SPLASH] state load idx={idx}")
                return {"index": idx}
    except Exception as e:
        logger.warning(f"[SPLASH] state load error: {e}")
    return {"index": 0}


def _save_splash_state(state: dict):
    """Escribe de forma atÃ³mica el estado de rotaciÃ³n."""
    try:
        path = Path(SPLASH_STATE_FILE)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        logger.info(f"[SPLASH] state save -> {state}")
    except Exception as e:
        logger.error(f"[SPLASH] state save error: {e}")
   
def _advance_splash_rotation():
    try:
        files = sorted(
            f for f in os.listdir(SPLASH_DIR)
            if f.lower().endswith(".mp4") and f.startswith("splash_")
        )
    except Exception as e:
        logger.error(f"[SPLASH] listar p/advance fallÃ³: {e}")
        files = []

    if not files:
        logger.info("[SPLASH] sin archivos, no avanzo")
        return

    st = _load_splash_state()
    idx = (st.get("index", 0) + 1) % len(files)
    _save_splash_state({"index": idx})
    logger.info(f"[SPLASH] advanced -> idx={idx}")

def _touch_frontend_ping(stage: str = None):
    """Marca ultimo ping del frontend y, opcionalmente, la etapa."""
    global _last_frontend_ping, _last_frontend_stage
    _last_frontend_ping = time.monotonic()
    if stage:
        _last_frontend_stage = stage


# --- GestiÃ³n: /gestion -------------------------------------------------

def _ctx_gestion():
    # Carga y saneos mÃ­nimos para que el dashboard estÃ© al dÃ­a
    global metadata
    metadata = load_metadata()
    ensure_durations()
    sanity_check_thumbnails()
    vids_ok, vids_fantasmas, vids_nuevos = sincronizar_videos()
    return dict(
        videos=vids_ok,
        fantasmas=vids_fantasmas,
        nuevos=vids_nuevos,
        tags=load_tags(),
        config=load_config(),
        recuerdos=get_total_recuerdos(),
        canales=load_canales(),
        active_page='library'
    )


@app.route("/")
def root():
    logger.info(_hdr("HIT /"))
    if os.path.exists(INTRO_FLAG):
        # ElegÃ­ una sola vez el splash y dejÃ¡ registro para este run
        path = get_next_splash_path()
        if path and os.path.isfile(path):
            logger.info("Intro flag presente -> /splash")
            return redirect(url_for("splash"))
        else:
            logger.info("Intro flag presente, pero sin splash vÃ¡lido -> /tv")
            return redirect(url_for("tv"))
    logger.info("Sin intro -> /tv")
    return redirect(url_for("tv"))


@app.route("/video/<video_id>")
def video_detail(video_id):
    video = metadata.get(video_id)
    if not video:
        return "Video no encontrado", 404

    # Determine video URL based on whether it's a series video
    series_path = video.get("series_path")
    if series_path:
        video_url = f"/videos/{series_path}.mp4"
    else:
        video_url = f"/videos/{video_id}.mp4"

    return render_template("video.html", video_id=video_id, video=video, video_url=video_url)

@app.route("/edit/<video_id>", methods=["GET", "POST"])
def edit_video(video_id):
    # Check if this is a series video
    existing_video = metadata.get(video_id, {})
    is_series_video = bool(existing_video.get("series_path"))

    if request.method == "POST":
        form = request.form
        tags = form.get("tags", "")

        # For series videos, category and series are locked
        if is_series_video:
            category = "tv_episode"
            video_data = {
                "title": form.get("title"),
                "tags": [tag.strip() for tag in tags.split(",") if tag.strip()],
                "personaje": form.get("personaje"),
                "fecha": form.get("fecha"),
                "modo": form.getlist("modo"),
                "category": "tv_episode",
                "series": existing_video.get("series"),
                "series_path": existing_video.get("series_path"),
            }
            # Season and episode can still be edited
            season_str = form.get("season", "").strip()
            episode_str = form.get("episode", "").strip()
            video_data["season"] = int(season_str) if season_str.isdigit() else None
            video_data["episode"] = int(episode_str) if episode_str.isdigit() else None
        else:
            category = form.get("category", "vhs_tape")
            video_data = {
                "title": form.get("title"),
                "tags": [tag.strip() for tag in tags.split(",") if tag.strip()],
                "personaje": form.get("personaje"),
                "fecha": form.get("fecha"),
                "modo": form.getlist("modo"),
                "category": category
            }
            # Non-series videos don't have TV Episode fields
            video_data["series"] = None
            video_data["season"] = None
            video_data["episode"] = None

        # Preserve existing fields like duracion
        if video_id in metadata:
            for key in ["duracion"]:
                if key in metadata[video_id]:
                    video_data[key] = metadata[video_id][key]

        metadata[video_id] = video_data
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        return redirect(url_for("index"))

    # Cargar video y tags
    if video_id in metadata:
        video = metadata[video_id]
    else:
        video = {
            "title": video_id.replace("_", " "),
            "tags": [],
            "personaje": "",
            "fecha": "",
            "modo": [],
            "category": "vhs_tape",
            "series": None,
            "season": None,
            "episode": None
        }

    selected_tags = video.get("tags", [])
    tags_data = load_tags()
    tag_categoria = {tag: grupo for grupo, info in tags_data.items() for tag in info["tags"]}

    # For series videos, get display name
    series_name = video.get("series", "")
    series_display = series_display_name(series_name) if series_name else ""

    return render_template("edit.html",
                       video_id=video_id,
                       video=video,
                       selected_tags=selected_tags,
                       tag_categoria=tag_categoria,
                       tags=tags_data,
                       is_series_video=is_series_video,
                       series_display=series_display)

@app.route("/api/videos")
def api_videos():
    return jsonify(metadata)

@app.route("/thumbnails/<filename>")
def serve_thumbnail(filename):
    return send_from_directory(os.path.join(CONTENT_DIR, "thumbnails"), filename)

@app.route("/videos/<filename>")
def serve_video(filename):
    return send_from_directory(os.path.join(CONTENT_DIR, "videos"), filename)

@app.route("/videos/series/<series_name>/<filename>")
def serve_series_video(series_name, filename):
    """Serve video files from series directories."""
    series_dir = SERIES_VIDEO_DIR / series_name
    return send_from_directory(str(series_dir), filename)


@app.route("/videos/system/<filename>")
def serve_system_video(filename):
    """Serve system video files (test pattern, sponsors placeholder)."""
    system_dir = VIDEO_DIR / "system"
    return send_from_directory(str(system_dir), filename)


@app.route("/videos/commercials/<filename>")
def serve_commercial_video(filename):
    """Serve commercial video files."""
    return send_from_directory(str(COMMERCIALS_DIR), filename)


@app.route("/delete_full/<video_id>")
def delete_full_video(video_id):
    # Check if it's a series video
    vid_info = metadata.get(video_id, {})
    series_path = vid_info.get("series_path")
    if series_path:
        video_path = str(VIDEO_DIR / f"{series_path}.mp4")
    else:
        video_path = os.path.join(VIDEO_DIR, video_id + ".mp4")
    if os.path.exists(video_path):
        os.remove(video_path)
        print(f"ðŸ§¨ Video eliminado: {video_path}")
    else:
        print(f"âš ï¸ Video no encontrado para: {video_id}")

    thumbnail_path = os.path.join(CONTENT_DIR, "thumbnails", video_id + ".jpg")
    if os.path.exists(thumbnail_path):
        os.remove(thumbnail_path)
        print(f"ðŸ§¹ Thumbnail eliminado: {thumbnail_path}")

    if video_id in metadata:
        del metadata[video_id]
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        print(f"âœ… Metadata eliminada: {video_id}")

    return redirect(url_for("index"))

@app.route("/delete/<video_id>")
def delete_video_metadata(video_id):
    removed_any = False
    if video_id in metadata:
        del metadata[video_id]
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        print(f"âœ… Metadata eliminada para: {video_id}")
        removed_any = True
    else:
        print(f"â„¹ï¸ No hay metadata para: {video_id}")

    thumbnail_path = os.path.join(CONTENT_DIR, "thumbnails", video_id + ".jpg")
    if os.path.exists(thumbnail_path):
        os.remove(thumbnail_path)
        print(f"ðŸ§¹ Thumbnail eliminado: {thumbnail_path}")
        removed_any = True
    else:
        print(f"â„¹ï¸ No se encontrÃ³ thumbnail para: {video_id}")

    if not removed_any:
        print(f"âš ï¸ Nada que borrar para: {video_id}")

    return redirect(url_for("index"))

@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "GET":
        return render_template("upload.html")

    files = request.files.getlist("videos[]")
    os.makedirs(VIDEO_DIR, exist_ok=True)

    print(f"ðŸ“¥ Archivos recibidos: {[f.filename for f in files]}")

    for file in files:
        if not file.filename.lower().endswith(".mp4"):
            escribir_estado(f"âŒ Archivo no permitido: {file.filename}")
            continue

        escribir_estado("ðŸ“¥ Recibiendo archivo...")

        filename = secure_filename(file.filename)
        video_id = os.path.splitext(filename)[0]
        final_path = os.path.join(VIDEO_DIR, video_id + ".mp4")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
            temp_path = tmp.name
            file.save(temp_path)

        print(f"ðŸ”„ Procesando: {filename}")
        try:
            escribir_estado("ðŸ“ Comprobando resoluciÃ³n...")
            width, height = get_video_resolution(temp_path)
            duracion = get_video_duration(temp_path)
            metadata[video_id] = metadata.get(video_id, {})
            metadata[video_id]["duracion"] = duracion
            if width == 800 and height == 480:
                shutil.copy(temp_path, final_path)
                escribir_estado("âœ… Video ya estaba en 800x480. Copiado directo")
                print(f"âœ… Video ya estaba en 800x480. Copiado directo: {final_path}")
            else:
                escribir_estado("âœ‚ï¸ Redimensionando video...")
                subprocess.run([
                    "ffmpeg", "-i", temp_path,
                    "-vf", "scale=800:480:force_original_aspect_ratio=decrease,pad=800:480:(ow-iw)/2:(oh-ih)/2",
                    "-c:a", "copy",
                    "-y", final_path
                ], check=True)
                print(f"ðŸŽ› Video procesado con resize y crop: {final_path}")
        except Exception as e:
            escribir_estado(f"âš ï¸ Error al procesar {filename}")
            print(f"âš ï¸ Error al procesar {filename}: {e}")
        finally:
            os.remove(temp_path)

        escribir_estado("ðŸ–¼ Generando thumbnail...")
        sanity_check_thumbnails(video_id)
        escribir_estado("âœ… Â¡Listo che! ðŸ§‰")

    eliminar_estado()
    return redirect(url_for("index"))

@app.route("/upload_status")
def upload_status():
    try:
        with open(UPLOAD_STATUS, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "Sin actividad"
        
@app.route("/tags")
def tags():
    tags_data = load_tags()
    return_to = request.args.get("return_to")
    return render_template("tags.html", tags=tags_data, return_to=return_to)

@app.route("/add_tag", methods=["POST"])
def add_tag():
    tag = request.form.get("tag", "").strip()
    group = request.form.get("group")
    return_to = request.form.get("from_edit")

    tags_data = load_tags()

    if not tag or not group:
        return redirect(url_for("tags", from_edit=return_to))

    if group in tags_data:
        if tag not in tags_data[group]["tags"]:
            tags_data[group]["tags"].append(tag)
    else:
        tags_data[group] = {"color": "#cccccc", "tags": [tag]}

    save_tags(tags_data)

    # Agregar a configuracion.json si no existe
    config_path = os.path.join(CONTENT_DIR, "configuracion.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {"tags_prioridad": [], "tags_incluidos": []}

    if tag not in config["tags_prioridad"]:
        config["tags_prioridad"].append(tag)
    if tag not in config["tags_incluidos"]:
        config["tags_incluidos"].append(tag)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    return redirect(url_for("tags", from_edit=return_to))

@app.route("/update_group_color", methods=["POST"])
def update_group_color():
    group = request.form.get("group")
    color = (request.form.get("color") or "#cccccc").strip()
    return_to = request.form.get("from_edit")

    if not group:
        return redirect(url_for("tags", from_edit=return_to))

    tags_data = load_tags()
    if group in tags_data:
        tags_data[group]["color"] = color
        save_tags(tags_data)  
    return redirect(url_for("tags", from_edit=return_to))
    
@app.route("/add_group", methods=["POST"])
def add_group():
    group = request.form.get("group").strip()
    color = request.form.get("color") or "#cccccc"
    tags_data = load_tags()

    if group and group not in tags_data:
        tags_data[group] = {"color": color, "tags": []}
        save_tags(tags_data)

    return redirect(url_for("tags"))

@app.route("/delete_tag", methods=["POST"])
def delete_tag():
    tag = request.form.get("tag")
    group = request.form.get("group")
    return_to = request.form.get("from_edit")  # Para redirigir correctamente

    if not tag or not group:
        return redirect(url_for("tags", from_edit=return_to))

    tags_data = load_tags()

    if group in tags_data and tag in tags_data[group]["tags"]:
        tags_data[group]["tags"].remove(tag)

        # TambiÃ©n eliminarlo de todos los metadata
        for video in metadata.values():
            if tag in video.get("tags", []):
                video["tags"].remove(tag)

        # Guardar ambos archivos
        save_tags(tags_data)
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        # Eliminar de configuracion.json tambiÃ©n
        config_path = os.path.join(CONTENT_DIR, "configuracion.json")
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)

            config["tags_prioridad"] = [t for t in config.get("tags_prioridad", []) if t != tag]
            config["tags_incluidos"] = [t for t in config.get("tags_incluidos", []) if t != tag]

            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

    return redirect(url_for("tags", from_edit=return_to))


@app.route("/delete_group", methods=["POST"])
def delete_group():
    group = request.form.get("group")
    return_to = request.form.get("from_edit")

    if not group:
        return redirect(url_for("tags", from_edit=return_to))

    tags_data = load_tags()

    if group in tags_data:
        backup_tags()  # antes de modificar nada

        # Tags del grupo a eliminar
        tags_to_remove = tags_data[group]["tags"]

        # Eliminar del metadata
        for video in metadata.values():
            video["tags"] = [t for t in video.get("tags", []) if t not in tags_to_remove]

        # Eliminar del tags.json
        del tags_data[group]
        save_tags(tags_data)

        # Eliminar del configuracion.json
        config = load_config()
        config["tags_prioridad"] = [t for t in config.get("tags_prioridad", []) if t not in tags_to_remove]
        config["tags_incluidos"] = [t for t in config.get("tags_incluidos", []) if t not in tags_to_remove]
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        # Guardar metadata
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        print(f"ðŸ—‘ Grupo eliminado: {group} (y sus tags)")

    return redirect(url_for("tags", from_edit=return_to))


@app.route("/configuracion", methods=["GET", "POST"])
def configuracion():
    tags_data = load_tags()
    config_data = load_config()

    # ðŸ’¡ Sanity check: remover tags que ya no existen
    config_data = clean_config_tags(tags_data, config_data)

    # Guardar si hubo algÃºn cambio
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)

    return render_template("configuracion.html", tags=tags_data, config=config_data)



@app.route("/guardar_configuracion", methods=["POST"])
def guardar_configuracion():
    prioridad = request.form.get("tags_prioridad", "")
    incluidos = request.form.getlist("tags_incluidos")

    # Solo mantener en prioridad los tags incluidos
    orden_final = [tag.strip() for tag in prioridad.split(",") if tag.strip() and tag.strip() in incluidos]

    config = {
        "tags_prioridad": orden_final,
        "tags_incluidos": incluidos
    }

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print("âœ… ConfiguraciÃ³n guardada")
    except Exception as e:
        print(f"âŒ Error al guardar configuraciÃ³n: {e}")

    return redirect(url_for("configuracion"))

@app.route("/vertele")
def vertele():
    canales = load_canales()

    canal_activo_path = str(CANAL_ACTIVO_FILE)
    canal_activo = None

    if not os.path.exists(canal_activo_path):
        # ElegÃ­ un id real: primero el de DEFAULT_CANAL_ACTIVO si existe, si no, el primer canal disponible
        preferido = DEFAULT_CANAL_ACTIVO.get("canal_id", "1")
        if preferido not in canales:
            preferido = next(iter(canales.keys()), "1")
        with open(canal_activo_path, "w", encoding="utf-8") as f:
            json.dump({"canal_id": preferido}, f, ensure_ascii=False, indent=2)
        canal_activo = preferido
    else:
        with open(canal_activo_path, "r", encoding="utf-8") as f:
            activo_data = json.load(f)
            canal_activo = activo_data.get("canal_id") or DEFAULT_CANAL_ACTIVO.get("canal_id", "1")
            if canal_activo not in canales and canales:
                canal_activo = next(iter(canales.keys()))
                set_canal_activo(canal_activo)  # persistÃ­ la migraciÃ³n

    return render_template("vertele.html",
                           canales=canales,
                           canal_activo=canal_activo)



@app.route("/api/next_video")
def api_next_video():
    # Check if we're on Channel 03 (system AV input channel)
    canal_activo_path = str(CANAL_ACTIVO_FILE)
    if os.path.exists(canal_activo_path):
        with open(canal_activo_path, "r", encoding="utf-8") as f:
            activo = json.load(f)
            if activo.get("canal_id") == "03":
                # Channel 03 is the AV input - frontend handles display based on VCR state
                return jsonify({
                    "channel_type": "av_input",
                    "modo": "03",
                    "canal_nombre": "03",
                    "canal_id": "03",
                    "canal_numero": "03",
                })

    # Check for broadcast TV scheduling
    # If channel has series_filter, use scheduled content instead of fairness-based selection
    canales = load_canales()
    if os.path.exists(canal_activo_path):
        with open(canal_activo_path, "r", encoding="utf-8") as f:
            activo = json.load(f)
            canal_id = activo.get("canal_id")
            if canal_id and canal_id in canales:
                config = canales[canal_id]
                if config.get("series_filter"):
                    # This is a broadcast TV channel - use scheduler
                    try:
                        scheduled = scheduler.get_scheduled_content(canal_id)
                        if scheduled:
                            logger.info(f"[NEXT] Broadcast channel {canal_id}: type={scheduled['type']}, video={scheduled['video_id']}, seek={scheduled.get('seek_to', 0)}")
                            return jsonify({
                                "video_id": scheduled["video_id"],
                                "video_url": scheduled["video_url"],
                                "seek_to": scheduled.get("seek_to", 0),
                                "title": scheduled.get("title", ""),
                                "tags": [],
                                "modo": canal_id,
                                "canal_nombre": config.get("nombre", canal_id),
                                "canal_numero": get_canal_numero(canal_id, canales),
                                "broadcast_type": scheduled["type"],
                                "is_broadcast": True
                            })
                    except Exception as e:
                        logger.error(f"[NEXT] Broadcast scheduling error for {canal_id}: {e}")
                        # Fall through to normal selection if scheduler fails

    metadata = load_metadata()

    global _force_next_once
    force_next = _force_next_once
    _force_next_once = False

    # Canal activo + config
    canal_id = "canal_base"
    config = load_config()

    if os.path.exists(canal_activo_path):
        with open(canal_activo_path, "r", encoding="utf-8") as f:
            activo = json.load(f)
            if activo.get("canal_id") in canales:
                canal_id = activo["canal_id"]
                config = canales[canal_id]
    
    # --- De-dupe: si hay pick pendiente "fresco", reusalo ---
    now = time.time()
    pp = pending_pick.get(canal_id)
    if (not force_next) and pp and (now - pp.get("ts", 0.0)) < PENDING_TTL:
        vid = pp["video_id"]
        info = metadata.get(vid, {})
        series_path = info.get("series_path")
        video_url = f"/videos/{series_path}.mp4" if series_path else f"/videos/{vid}.mp4"
        logger.info(f"[NEXT-DUPE] Reuso pick pendiente canal={canal_id} video={vid}")
        return jsonify({
            "video_id": vid,
            "video_url": video_url,
            "title": info.get("title", vid.replace("_", " ")),
            "tags": info.get("tags", []),
            "modo": canal_id,
            "canal_nombre": canales[canal_id].get("nombre", canal_id),
            "canal_numero": get_canal_numero(canal_id, canales),
            "reused": True,
            "do_not_restart": True
        })

    # --- Sticky window ---
    now = time.time()
    sticky = last_choice_per_canal.get(canal_id)
    if (not force_next) and sticky and (now - sticky["ts"]) < STICKY_WINDOW:
        elegido_id = sticky["video_id"]
        elegido_data = metadata.get(elegido_id, {})
        if elegido_data:
            series_path = elegido_data.get("series_path")
            video_url = f"/videos/{series_path}.mp4" if series_path else f"/videos/{elegido_id}.mp4"
            return jsonify({
                "video_id": elegido_id,
                "video_url": video_url,
                "title": elegido_data.get("title", elegido_id.replace("_", " ")),
                "tags": elegido_data.get("tags", []),
                "score_tags": 0,
                "fair_plays_norm": 0.0,
                "fair_last_ts": 0.0,
                "modo": canal_id,
                "canal_nombre": canales[canal_id].get("nombre", canal_id),
                "canal_numero": get_canal_numero(canal_id, canales),
                "sticky": True
            })

    # --- Cooldown por canal ---
    now = time.time()
    ultimo = last_next_call.get(canal_id, 0.0)
    sticky = last_choice_per_canal.get(canal_id)
    if (not force_next) and (now - ultimo) < NEXT_COOLDOWN and sticky and (now - sticky["ts"]) >= STICKY_WINDOW:
        logger.info(f"[NEXT] cooldown canal={canal_id} dt={now-ultimo:.2f}s -> bloqueo")
        return jsonify({"cooldown": True, "canal_id": canal_id}), 200

    # --- Series filter: if channel has series_filter, only show TV Episodes from those series ---
    series_filter = config.get("series_filter", [])
    series_filter_set = set(series_filter) if series_filter else None

    prioridad = config.get("tags_prioridad", [])
    incluidos = set(config.get("tags_incluidos", prioridad))  # fallback

    # Only require tags_incluidos for non-series channels
    if not incluidos and not series_filter_set:
        return jsonify({"error": "No hay tags incluidos definidos en la configuración."}), 400

    canal_shown = shown_videos_por_canal.get(canal_id, [])

    # ====== (ya lo tenías) tags del último video del canal ======
    prev = last_choice_per_canal.get(canal_id)
    last_tags = set()
    if prev:
        prev_md = metadata.get(prev["video_id"], {})
        last_tags = set(prev_md.get("tags", []))

    # Debug logging for series filter
    if series_filter_set:
        tv_episodes = [(vid, d.get("series")) for vid, d in metadata.items() if d.get("category") == "tv_episode"]
        logger.info(f"[NEXT] Series filter active: {series_filter}, found {len(tv_episodes)} TV episodes in metadata")
        for vid, ser in tv_episodes[:5]:  # Log first 5
            logger.info(f"[NEXT]   - {vid}: series={ser}, match={ser in series_filter_set if ser else False}")

    # --- Candidatos por tags e inéditos en el canal ---
    candidatos = []
    for video_id, data in metadata.items():
        if video_id in canal_shown:
            continue

        # Series filter: when active, only include TV Episodes with matching series
        if series_filter_set:
            if data.get("category") != "tv_episode":
                continue
            video_series = data.get("series")
            if not video_series or video_series not in series_filter_set:
                continue
            # Series videos don't require tag matching - include them directly
            video_tags = set(data.get("tags", []))
            tag_score = sum((len(prioridad) - prioridad.index(tag)) for tag in video_tags if tag in prioridad)
            overlap = len(video_tags & last_tags)
            candidatos.append((video_id, data, tag_score, video_tags, overlap))
            continue

        # Non-series: require tag matching
        video_tags = set(data.get("tags", []))
        if not (video_tags & incluidos):
            continue
        tag_score = sum((len(prioridad) - prioridad.index(tag)) for tag in video_tags if tag in prioridad)
        overlap = len(video_tags & last_tags)
        candidatos.append((video_id, data, tag_score, video_tags, overlap))

    # ====== NUEVO: Anti-repetición por tiempo mínimo global ======
    # Podés configurar por canal en canales.json: {"min_gap_minutes": 60}
    MIN_GAP_MIN = int(config.get("min_gap_minutes", 60))  # default 60 min si no se define
    MIN_GAP_SEC = max(0, MIN_GAP_MIN) * 60

    plays_map = load_plays()  # << movido arriba para usarlo en el filtro
    now_ts = time.time()

    def _age_seconds(vid):
        p = plays_map.get(vid, {})
        last_ts = _iso_to_ts(p.get("last_played")) if p.get("last_played") else 0.0
        return float('inf') if last_ts == 0 else (now_ts - last_ts)

    # 1) Filtrado estricto: excluir todo lo “demasiado fresco”
    candidatos_ok = [t for t in candidatos if _age_seconds(t[0]) >= MIN_GAP_SEC]
    # Guardamos también los rechazados para fallback
    candidatos_frescos = [t for t in candidatos if _age_seconds(t[0]) <  MIN_GAP_SEC]

    # Si te quedaste sin nada por el gap, relajamos: usamos los “menos frescos” primero
    if not candidatos_ok and candidatos_frescos:
        logger.info(f"[NEXT] Relax gap: no candidates >= {MIN_GAP_MIN} min; usando los más antiguos entre los frescos")
        # Ordenar frescos por mayor edad primero (los que están más cerca de cumplir el gap)
        candidatos_ok = sorted(
            candidatos_frescos,
            key=lambda t: (_age_seconds(t[0]) * -1)  # mayor edad -> primero
        )

    # Reemplazamos la lista original por la filtrada/relajada
    candidatos = candidatos_ok
    # ============================================================

    # Si no quedan, limpiá "ya vistos" y reintentá
    if not candidatos:
        if canal_shown:
            shown_videos_por_canal[canal_id] = []
            return api_next_video()
        else:
            logger.warning(f"[NEXT] No videos found for canal={canal_id}, series_filter={series_filter}")
            return jsonify({"no_videos": True, "canal_id": canal_id})

    # --- Fairness + diversidad + prioridad + jitter ---
    def sort_key(t):
        video_id, data, tag_score, video_tags, overlap = t
        plays_norm, last_ts, jitter = score_for_video(video_id, metadata, plays_map)
        # ascendente: plays_norm, menos reciente, menos overlap, más prioridad, jitter
        return (plays_norm, last_ts, overlap, -tag_score, jitter)

    candidatos.sort(key=sort_key)

    elegido_id, elegido_data, tag_score, elegido_tags, elegido_overlap = candidatos[0]
    pending_pick[canal_id] = {"video_id": elegido_id, "ts": time.time()}

    canal_shown.append(elegido_id)
    shown_videos_por_canal[canal_id] = canal_shown

    last_next_call[canal_id] = time.time()

    fair_plays_norm, fair_last_ts, _ = score_for_video(elegido_id, metadata, plays_map)
    edad_s = _age_seconds(elegido_id)  # para debug
    logger.info(f"[NEXT] canal={canal_id} elegido={elegido_id} tagscore={tag_score} plays_norm={fair_plays_norm:.3f} overlap_prev={elegido_overlap} age={edad_s:.0f}s gap={MIN_GAP_MIN}m")

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] [API] Reproduciendo video: {elegido_id} del canal {canal_id}")
    last_choice_per_canal[canal_id] = {"video_id": elegido_id, "ts": time.time()}

    # Determine video path - series videos use series_path, regular videos use video_id
    series_path = elegido_data.get("series_path")
    video_url = f"/videos/{series_path}.mp4" if series_path else f"/videos/{elegido_id}.mp4"

    return jsonify({
        "video_id": elegido_id,
        "video_url": video_url,
        "title": elegido_data.get("title", elegido_id.replace("_", " ")),
        "tags": elegido_data.get("tags", []),
        "score_tags": tag_score,
        "fair_plays_norm": fair_plays_norm,
        "fair_last_ts": fair_last_ts,
        "overlap_prev": elegido_overlap,
        "modo": canal_id,
        "canal_nombre": canales[canal_id].get("nombre", canal_id),
        "canal_numero": get_canal_numero(canal_id, canales),
        "min_gap_minutes": MIN_GAP_MIN,        # debug útil
        "age_seconds": None if edad_s == float('inf') else int(edad_s)
    })



def _get_all_series():
    """Get list of series names from series.json."""
    return sorted(load_series().keys())

def _get_series_episode_count(series_name):
    """Count episodes for a given series."""
    count = 0
    meta = load_metadata()
    for video_id, data in meta.items():
        if data.get("series") == series_name:
            count += 1
    return count

# ============================================================================
# SERIES MANAGEMENT ROUTES
# ============================================================================

@app.route("/series")
def series_page():
    """Series management page."""
    series_data = load_series()

    # Build list with episode counts and display names
    series_list = []
    for folder_name, info in series_data.items():
        series_list.append({
            "folder_name": folder_name,
            "display_name": series_display_name(folder_name),
            "episode_count": _get_series_episode_count(folder_name),
            "created": info.get("created", ""),
            "time_of_day": info.get("time_of_day", "any")
        })

    # Sort by display name
    series_list.sort(key=lambda s: s["display_name"].lower())

    cfg = load_config_i18n()
    lang = cfg.get("language", "es")
    base_trans = load_translations(lang)

    def tr(key, default=None):
        keys = key.split(".")
        val = base_trans
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default if default else key
        return val if val else (default if default else key)

    return render_template("series.html",
                           series_list=series_list,
                           lang=lang,
                           tr=tr)

@app.route("/series/add", methods=["POST"])
def series_add():
    """Add a new series."""
    display_name = request.form.get("name", "").strip()
    if not display_name:
        return redirect(url_for("index"))

    folder_name = series_folder_name(display_name)
    if not folder_name:
        return redirect(url_for("index"))

    series_data = load_series()

    # Check if already exists
    if folder_name in series_data:
        return redirect(url_for("index"))

    # Create folder
    series_dir = SERIES_VIDEO_DIR / folder_name
    series_dir.mkdir(parents=True, exist_ok=True)

    # Add to series.json with default time_of_day
    series_data[folder_name] = {
        "created": datetime.now().strftime("%Y-%m-%d"),
        "time_of_day": "any"  # Default: can play at any time
    }
    save_series(series_data)

    logger.info(f"[SERIES] Created new series: {display_name} ({folder_name})")
    return redirect(url_for("index"))

@app.route("/series/delete/<series_name>", methods=["POST"])
def series_delete(series_name):
    """Delete a series and all its episodes."""
    series_data = load_series()

    if series_name not in series_data:
        return redirect(url_for("series_page"))

    # Delete folder and contents
    series_dir = SERIES_VIDEO_DIR / series_name
    if series_dir.exists():
        shutil.rmtree(series_dir)

    # Remove from series.json
    del series_data[series_name]
    save_series(series_data)

    # Remove related metadata entries
    global metadata
    metadata = load_metadata()
    to_delete = [vid for vid, data in metadata.items() if data.get("series") == series_name]
    for vid in to_delete:
        del metadata[vid]
        # Also delete thumbnail
        thumb_path = THUMB_DIR / f"{vid}.jpg"
        if thumb_path.exists():
            thumb_path.unlink()

    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    logger.info(f"[SERIES] Deleted series: {series_name} ({len(to_delete)} episodes removed)")
    return redirect(url_for("series_page"))

@app.route("/api/series")
def api_series():
    """Return list of series with episode counts, time_of_day, and episodes grouped by season."""
    series_data = load_series()
    global metadata
    metadata = load_metadata()

    result = []
    for name in sorted(series_data.keys()):
        # Get all episodes for this series
        episodes = []
        for video_id, v in metadata.items():
            if v.get("category") == "tv_episode" and v.get("series") == name:
                episodes.append({
                    "video_id": video_id,
                    "title": v.get("title", video_id),
                    "season": v.get("season") or 1,
                    "episode": v.get("episode") or 0,
                    "duration": v.get("duracion", 0)
                })

        # Group episodes by season
        seasons = {}
        for ep in episodes:
            season_num = ep["season"]
            if season_num not in seasons:
                seasons[season_num] = []
            seasons[season_num].append(ep)

        # Sort episodes within each season
        for season_num in seasons:
            seasons[season_num].sort(key=lambda x: (x["episode"], x["title"]))

        # Convert to sorted list of season objects
        seasons_list = [
            {"season": s, "episodes": seasons[s]}
            for s in sorted(seasons.keys())
        ]

        result.append({
            "folder_name": name,
            "display_name": series_display_name(name),
            "episode_count": len(episodes),
            "time_of_day": series_data[name].get("time_of_day", "any"),
            "created": series_data[name].get("created", ""),
            "seasons": seasons_list
        })

    return jsonify({"ok": True, "series": result})


@app.route("/api/series/time_of_day", methods=["POST"])
def api_series_time_of_day():
    """Update time-of-day preference for a series."""
    data = request.get_json() or request.form
    series_name = data.get("series_name")
    time_of_day = data.get("time_of_day")

    if not series_name or not time_of_day:
        return jsonify({"error": "Missing series_name or time_of_day"}), 400

    valid_options = ["early_morning", "late_morning", "afternoon", "evening", "night", "any"]
    if time_of_day not in valid_options:
        return jsonify({"error": f"Invalid time_of_day. Valid options: {valid_options}"}), 400

    series_data = load_series()
    if series_name not in series_data:
        return jsonify({"error": f"Series not found: {series_name}"}), 404

    series_data[series_name]["time_of_day"] = time_of_day
    save_series(series_data)

    logger.info(f"[SERIES] Updated time_of_day for {series_name} to {time_of_day}")
    return jsonify({"ok": True, "series_name": series_name, "time_of_day": time_of_day})


# ============================================================================
# SERIES UPLOAD ROUTES
# ============================================================================

@app.route("/upload/series", methods=["GET"])
def upload_series():
    """Series upload page."""
    series_data = load_series()

    # Build list with display names
    series_list = [
        {
            "folder_name": name,
            "display_name": series_display_name(name)
        }
        for name in sorted(series_data.keys())
    ]

    # Check if a specific series was requested
    preselected = request.args.get("series", "")

    cfg = load_config_i18n()
    lang = cfg.get("language", "es")
    base_trans = load_translations(lang)

    def tr(key, default=None):
        keys = key.split(".")
        val = base_trans
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default if default else key
        return val if val else (default if default else key)

    # Get all existing video IDs for client-side duplicate detection
    metadata = load_metadata()
    existing_ids = list(metadata.keys())

    return render_template("upload_series.html",
                           series_list=series_list,
                           preselected=preselected,
                           existing_ids=existing_ids,
                           lang=lang,
                           tr=tr)

@app.route("/upload/series", methods=["POST"])
def upload_series_post():
    """Handle series episode uploads with H.264 verification."""
    global metadata

    # Get series - either existing or new
    series_name = request.form.get("series", "").strip()
    new_series_name = request.form.get("new_series", "").strip()

    if new_series_name:
        # Create new series
        folder_name = series_folder_name(new_series_name)
        if folder_name:
            series_data = load_series()
            if folder_name not in series_data:
                series_dir = SERIES_VIDEO_DIR / folder_name
                series_dir.mkdir(parents=True, exist_ok=True)
                series_data[folder_name] = {
                    "created": datetime.now().strftime("%Y-%m-%d")
                }
                save_series(series_data)
                logger.info(f"[SERIES] Created new series during upload: {new_series_name}")
            series_name = folder_name
    elif not series_name:
        return jsonify({"ok": False, "error": "No series selected"}), 400

    # Verify series exists
    series_data = load_series()
    if series_name not in series_data:
        return jsonify({"ok": False, "error": "Series not found"}), 404

    series_dir = SERIES_VIDEO_DIR / series_name
    series_dir.mkdir(parents=True, exist_ok=True)

    files = request.files.getlist("videos[]")
    if not files:
        return jsonify({"ok": False, "error": "No files provided"}), 400

    metadata = load_metadata()
    results = []

    for file in files:
        if not file.filename:
            continue
        if not file.filename.lower().endswith(".mp4"):
            results.append({
                "filename": file.filename,
                "ok": False,
                "error": "Only .mp4 files are allowed"
            })
            continue

        # Sanitize filename
        original_name = secure_filename(file.filename)
        video_id = os.path.splitext(original_name)[0]
        # Ensure no spaces
        video_id = video_id.replace(' ', '_')
        final_path = series_dir / f"{video_id}.mp4"

        # Check for duplicate
        if video_id in metadata:
            results.append({
                "filename": file.filename,
                "ok": False,
                "error": "already_exists",
                "video_id": video_id
            })
            continue

        # Save to temp file first for verification
        temp_path = str(final_path) + ".uploading"
        file.save(temp_path)

        try:
            # Verify H.264 codec
            is_valid, error_msg = verify_h264_codec(temp_path)
            if not is_valid:
                os.remove(temp_path)
                results.append({
                    "filename": file.filename,
                    "ok": False,
                    "error": error_msg
                })
                continue

            # Get duration for metadata
            duracion = get_video_duration(temp_path)

            # Move to final location (no transcoding)
            shutil.move(temp_path, final_path)

            # Parse season/episode from filename
            season, episode = parse_episode_info(video_id)

            # Create metadata
            series_path = f"series/{series_name}/{video_id}"
            metadata[video_id] = {
                "title": video_id,
                "category": "tv_episode",
                "series": series_name,
                "series_path": series_path,
                "season": season,
                "episode": episode,
                "tags": [],
                "personaje": "",
                "fecha": "",
                "modo": [],
                "duracion": duracion
            }

            # Generate thumbnail
            thumb_path = THUMB_DIR / f"{video_id}.jpg"
            try:
                generate_thumbnail(str(final_path), str(thumb_path))
            except Exception as e:
                logger.warning(f"[SERIES] Failed to generate thumbnail for {video_id}: {e}")

            logger.info(f"[SERIES] Uploaded episode: {series_path}")
            results.append({
                "filename": file.filename,
                "ok": True,
                "video_id": video_id,
                "duration": duracion
            })

        except Exception as e:
            logger.error(f"[SERIES] Error processing {file.filename}: {e}")
            results.append({
                "filename": file.filename,
                "ok": False,
                "error": str(e)
            })
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    # Save metadata
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    return jsonify({"ok": True, "results": results})


# =============================================================================
# COMMERCIAL UPLOAD
# =============================================================================

def get_commercials_list():
    """Get list of all commercials from metadata."""
    metadata = load_metadata()
    commercials = []
    for video_id, data in metadata.items():
        if data.get("category") == "commercial":
            commercials.append({
                "video_id": video_id,
                "title": data.get("title", video_id),
                "duration": data.get("duracion", 0),
                "tags": data.get("tags", []),
                "fecha": data.get("fecha", ""),
            })
    # Sort by title
    commercials.sort(key=lambda x: x["title"].lower())
    return commercials


@app.route("/upload/commercials", methods=["GET"])
def upload_commercials():
    """Commercial upload page."""
    cfg = load_config_i18n()
    lang = cfg.get("language", "es")
    base_trans = load_translations(lang)

    def tr(key, default=None):
        keys = key.split(".")
        val = base_trans
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default if default else key
        return val if val else (default if default else key)

    commercials = get_commercials_list()

    # Get all existing video IDs for client-side duplicate detection
    metadata = load_metadata()
    existing_ids = list(metadata.keys())

    return render_template("upload_commercials.html",
                           commercials=commercials,
                           existing_ids=existing_ids,
                           lang=lang,
                           tr=tr)


@app.route("/upload/commercials", methods=["POST"])
def upload_commercials_post():
    """Handle commercial uploads with H.264 verification."""
    global metadata

    # Ensure commercials directory exists
    COMMERCIALS_DIR.mkdir(parents=True, exist_ok=True)

    files = request.files.getlist("videos[]")
    if not files:
        return jsonify({"ok": False, "error": "No files provided"}), 400

    metadata = load_metadata()
    results = []

    for file in files:
        if not file.filename:
            continue
        if not file.filename.lower().endswith(".mp4"):
            results.append({
                "filename": file.filename,
                "ok": False,
                "error": "Only .mp4 files are allowed"
            })
            continue

        # Sanitize filename
        original_name = secure_filename(file.filename)
        video_id = os.path.splitext(original_name)[0]
        video_id = video_id.replace(' ', '_')
        final_path = COMMERCIALS_DIR / f"{video_id}.mp4"

        # Check for duplicate
        if video_id in metadata:
            results.append({
                "filename": file.filename,
                "ok": False,
                "error": "already_exists",
                "video_id": video_id
            })
            continue

        # Save to temp file first for verification
        temp_path = str(final_path) + ".uploading"
        file.save(temp_path)

        try:
            # Verify H.264 codec
            is_valid, error_msg = verify_h264_codec(temp_path)
            if not is_valid:
                os.remove(temp_path)
                results.append({
                    "filename": file.filename,
                    "ok": False,
                    "error": error_msg
                })
                continue

            # Get duration for metadata
            duracion = get_video_duration(temp_path)

            # Move to final location (no transcoding)
            shutil.move(temp_path, final_path)

            # Create metadata with commercials path
            commercials_path = f"commercials/{video_id}"
            metadata[video_id] = {
                "title": video_id,
                "category": "commercial",
                "commercials_path": commercials_path,
                "tags": [],
                "personaje": "",
                "fecha": "",
                "modo": [],
                "duracion": duracion
            }

            # Generate thumbnail
            thumb_path = THUMB_DIR / f"{video_id}.jpg"
            try:
                generate_thumbnail(str(final_path), str(thumb_path))
            except Exception as e:
                logger.warning(f"[COMMERCIALS] Failed to generate thumbnail for {video_id}: {e}")

            logger.info(f"[COMMERCIALS] Uploaded commercial: {video_id} ({duracion:.1f}s)")
            results.append({
                "filename": file.filename,
                "ok": True,
                "video_id": video_id,
                "duration": duracion
            })

        except Exception as e:
            logger.error(f"[COMMERCIALS] Error processing {file.filename}: {e}")
            results.append({
                "filename": file.filename,
                "ok": False,
                "error": str(e)
            })
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    # Save metadata
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    return jsonify({"ok": True, "results": results})


@app.route("/api/commercials/<video_id>", methods=["DELETE"])
def delete_commercial(video_id):
    """Delete a commercial (file, metadata, and thumbnail)."""
    global metadata
    metadata = load_metadata()

    if video_id not in metadata:
        return jsonify({"ok": False, "error": "Commercial not found"}), 404

    video_data = metadata[video_id]
    if video_data.get("category") != "commercial":
        return jsonify({"ok": False, "error": "Video is not a commercial"}), 400

    try:
        # Delete video file
        video_path = COMMERCIALS_DIR / f"{video_id}.mp4"
        if video_path.exists():
            video_path.unlink()

        # Delete thumbnail
        thumb_path = THUMB_DIR / f"{video_id}.jpg"
        if thumb_path.exists():
            thumb_path.unlink()

        # Delete metadata
        del metadata[video_id]
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        logger.info(f"[COMMERCIALS] Deleted commercial: {video_id}")
        return jsonify({"ok": True})

    except Exception as e:
        logger.error(f"[COMMERCIALS] Error deleting {video_id}: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/commercials")
def api_commercials():
    """Return list of commercials for Content Manager."""
    global metadata
    metadata = load_metadata()

    commercials = []
    for video_id, data in metadata.items():
        if data.get("category") == "commercial":
            commercials.append({
                "video_id": video_id,
                "title": data.get("title", video_id),
                "duration": data.get("duracion", 0),
                "tags": data.get("tags", [])
            })

    # Sort by title
    commercials.sort(key=lambda x: x["title"].lower())

    return jsonify({"ok": True, "commercials": commercials})


# --- Movies API ---
@app.route("/api/movies")
def api_movies():
    """Return list of movies."""
    global metadata
    metadata = load_metadata()

    movies = []
    for video_id, data in metadata.items():
        if data.get("category") == "movie":
            movies.append({
                "video_id": video_id,
                "title": data.get("title", video_id),
                "duration": data.get("duracion", 0)
            })

    movies.sort(key=lambda x: x["title"].lower())
    return jsonify({"ok": True, "movies": movies})


@app.route("/api/movies/<video_id>", methods=["DELETE"])
def delete_movie(video_id):
    """Delete a movie (file, metadata, and thumbnail)."""
    global metadata
    metadata = load_metadata()

    if video_id not in metadata:
        return jsonify({"ok": False, "error": "Movie not found"}), 404

    video_data = metadata[video_id]
    if video_data.get("category") != "movie":
        return jsonify({"ok": False, "error": "Video is not a movie"}), 400

    try:
        # Delete video file
        video_path = VIDEO_DIR / f"{video_id}.mp4"
        if video_path.exists():
            video_path.unlink()

        # Delete thumbnail
        thumb_path = THUMB_DIR / f"{video_id}.jpg"
        if thumb_path.exists():
            thumb_path.unlink()

        # Delete metadata
        del metadata[video_id]
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        logger.info(f"[MOVIES] Deleted movie: {video_id}")
        return jsonify({"ok": True})

    except Exception as e:
        logger.error(f"[MOVIES] Error deleting {video_id}: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/upload/movies", methods=["POST"])
def upload_movies_post():
    """Handle movie file uploads."""
    global metadata
    metadata = load_metadata()

    files = request.files.getlist("videos[]")
    if not files:
        return jsonify({"ok": False, "error": "No files provided"}), 400

    results = []

    for file in files:
        if not file.filename:
            continue

        try:
            # Get video_id from filename
            video_id = secure_filename(file.filename).rsplit(".", 1)[0]

            # Check if already exists
            if video_id in metadata:
                results.append({"filename": file.filename, "ok": False, "error": "already_exists"})
                continue

            # Save to temp, then move
            temp_path = VIDEO_DIR / f"_temp_{video_id}.mp4"
            final_path = VIDEO_DIR / f"{video_id}.mp4"

            file.save(temp_path)

            # Get duration
            duration = get_video_duration(temp_path)
            if duration is None:
                duration = 0

            # Move to final location
            temp_path.rename(final_path)

            # Generate thumbnail
            generate_thumbnail(final_path, THUMB_DIR / f"{video_id}.jpg")

            # Create metadata
            title = video_id.replace("_", " ").replace("-", " ").title()
            metadata[video_id] = {
                "title": title,
                "category": "movie",
                "duracion": duration,
                "fecha": datetime.now().strftime("%Y-%m-%d"),
                "tags": [],
                "personaje": "",
                "modo": []
            }

            results.append({"filename": file.filename, "ok": True, "video_id": video_id})
            logger.info(f"[MOVIES] Uploaded: {video_id} ({duration}s)")

        except Exception as e:
            logger.error(f"[MOVIES] Error processing {file.filename}: {e}")
            results.append({"filename": file.filename, "ok": False, "error": str(e)})

    # Save metadata
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    return jsonify({"ok": True, "results": results})


@app.route("/canales")
def canales():
    canales_data = load_canales()

    # Get series with display names
    all_series = [
        {"folder_name": name, "display_name": series_display_name(name)}
        for name in _get_all_series()
    ]

    return render_template("canales.html", canales=canales_data, all_series=all_series, active_page='channels')

@app.route("/guardar_canal", methods=["POST"])
def guardar_canal():
    canal_id = request.form.get("canal_id")
    nombre = request.form.get("nombre", "").strip()
    descripcion = request.form.get("descripcion", "").strip()
    icono = request.form.get("icono", "").strip()
    tags_prioridad = request.form.getlist("tags_prioridad")
    series_filter = request.form.getlist("series_filter")
    intro = request.form.get("intro_video_id", "").strip()

    if not nombre:
        return redirect(url_for("canales"))

    canales = load_canales()

    # Si es nuevo, generar ID automÃ¡ticamente
    if not canal_id:
        existing_ids = [int(k) for k in canales.keys() if k.isdigit()]
        canal_id = str(max(existing_ids, default=0) + 1)

    nuevo_canal = {
        "nombre": nombre,
        "descripcion": descripcion,
        "icono": icono,
        "tags_prioridad": tags_prioridad,
        "series_filter": series_filter
    }

    if intro:
        nuevo_canal["intro_video_id"] = intro

    canales[canal_id] = nuevo_canal
    save_canales(canales)
    return redirect(url_for("canales"))


@app.route("/eliminar_canal/<canal_id>", methods=["POST"])
def eliminar_canal(canal_id):
    canales = load_canales()
    if canal_id in canales:
        del canales[canal_id]
        save_canales(canales)
    return redirect(url_for("canales"))

@app.route("/editar_canal/<canal_id>")
def editar_canal(canal_id):
    # Editing is now inline, redirect to main channels page
    return redirect(url_for("canales"))

@app.route("/api/set_canal_activo", methods=["POST"])
def api_set_canal_activo():
    data = request.get_json()
    canal_id = data.get("canal_id")
    if not canal_id:
        return jsonify({"error": "Canal no especificado"}), 400

    canales = load_canales()
    if canal_id != "base" and canal_id not in canales:
        return jsonify({"error": "Canal no vÃ¡lido"}), 404

    set_canal_activo(canal_id)
    return jsonify({"ok": True, "canal_id": canal_id})

@app.route("/api/canales")
def api_canales():
    canales_data = load_canales()
    canal_activo = get_canal_activo()

    canales_list = []
    for canal_id, canal_info in canales_data.items():
        canales_list.append({
            "id": canal_id,
            "nombre": canal_info.get("nombre", canal_id),
            "icono": canal_info.get("icono", "ðŸ“º")
        })

    nombre_activo = canales_data.get(canal_activo, {}).get("nombre", "Canal Base")

    return jsonify({
        "canales": canales_list,
        "canal_activo_nombre": nombre_activo
    })
    
@app.route("/tv")
def tv():
    logger.info(_hdr("HIT /tv (render player)"))
    global metadata

    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    else:
        metadata = {}

    # Tus funciones existentes
    ensure_durations()
    sanity_check_thumbnails()
    videos_validos, videos_fantasmas, videos_nuevos = sincronizar_videos()

    return render_template(
        "player.html",   
        videos=videos_validos,
        fantasmas=videos_fantasmas,
        nuevos=videos_nuevos,
        recuerdos=get_total_recuerdos()
    )

    
@app.route("/api/should_reload")
def api_should_reload():
    global _last_trigger_mtime_served, _last_trigger_reason, _last_trigger_mtime, _force_next_once

    if not os.path.exists(TRIGGER_PATH):
        return jsonify({"should_reload": False})

    mtime = os.path.getmtime(TRIGGER_PATH)

    # Disparar SOLO si hay un mtime nuevo que no se sirvió aún
    if mtime > _last_trigger_mtime_served:
        # Leer la razón del trigger (si está)
        try:
            with open(TRIGGER_PATH, "r") as f:
                data = json.load(f)
            _last_trigger_reason = data.get("reason", "")
        except Exception:
            _last_trigger_reason = ""

        _last_trigger_mtime = mtime

        # Si viene del botón físico, forzamos el próximo NEXT
        if _last_trigger_reason == "BTN_NEXT":
            _force_next_once = True

        _last_trigger_mtime_served = mtime
        return jsonify({"should_reload": True})

    return jsonify({"should_reload": False})



@app.route("/api/volumen", methods=["GET", "POST"])
def api_volumen():
    if request.method == "POST":
        data = request.get_json()
        nuevo_valor = max(0, min(100, data.get("valor", 50)))  # rango 0â€“100
        with open(VOLUMEN_PATH, "w") as f:
            json.dump({"valor": nuevo_valor}, f)
        return jsonify({"ok": True, "valor": nuevo_valor})

    # mÃ©todo GET
    if os.path.exists(VOLUMEN_PATH):
        with open(VOLUMEN_PATH, "r") as f:
            return jsonify(json.load(f))
    else:
        return jsonify({"valor": 50})

@app.route("/api/volumen_ping")
def api_volumen_ping():
    path = "/tmp/trigger_volumen.json"
    if not os.path.exists(path):
        return jsonify({"ping": False})
    mtime = os.path.getmtime(path)
    if time.time() - mtime < 1.0:
        return jsonify({"ping": True})
    return jsonify({"ping": False})
    
    
@app.route("/api/menu_ping")
def api_menu_ping():
    """
    Devuelve True si hubo un 'touch' reciente del encoder para abrir/confirmar menÃº.
    Recomendado: el proceso del encoder escribe/actualiza MENU_TRIGGER_PATH
    al detectar flanco de bajada SIN giro previo.
    """
    global _last_menu_mtime_served
    path = "/tmp/trigger_menu.json"
    if not os.path.exists(path):
        return jsonify({"ping": False})

    mtime = os.path.getmtime(path)

    # Sirve una sola vez por cada nuevo mtime (borde ascendente)
    if mtime > _last_menu_mtime_served:
        _last_menu_mtime_served = mtime
        return jsonify({"ping": True, "ts": mtime})

    return jsonify({"ping": False})
    
@app.route("/api/menu_state", methods=["GET", "POST"])
def api_menu_state():
    if request.method == "POST":
        data = request.get_json(force=True)
        open_flag = bool(data.get("open", False))
        with open(MENU_STATE_PATH, "w") as f:
            json.dump({"open": open_flag, "ts": time.time()}, f)
        return jsonify({"ok": True})
    # GET
    if os.path.exists(MENU_STATE_PATH):
        with open(MENU_STATE_PATH, "r") as f:
            return jsonify(json.load(f))
    return jsonify({"open": False})
    
@app.route("/api/menu_nav")
def api_menu_nav():
    """One-shot: devuelve delta (+1/-1) una sola vez por trigger"""
    global _last_nav_mtime_served
    if not os.path.exists(MENU_NAV_PATH):
        return jsonify({"ping": False})
    mtime = os.path.getmtime(MENU_NAV_PATH)
    if mtime > _last_nav_mtime_served:
        _last_nav_mtime_served = mtime
        with open(MENU_NAV_PATH, "r") as f:
            data = json.load(f)
        return jsonify({"ping": True, "delta": data.get("delta", 0), "ts": mtime})
    return jsonify({"ping": False})
    
@app.route("/api/menu_select")
def api_menu_select():
    """One-shot: confirma selecciÃ³n actual"""
    global _last_sel_mtime_served
    if not os.path.exists(MENU_SELECT_PATH):
        return jsonify({"ping": False})
    mtime = os.path.getmtime(MENU_SELECT_PATH)
    if mtime > _last_sel_mtime_served:
        _last_sel_mtime_served = mtime
        return jsonify({"ping": True, "ts": mtime})
    return jsonify({"ping": False})
    
@app.route("/api/ui_prefs", methods=["GET", "POST"])
def api_ui_prefs():
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        save_ui_prefs(data)
        return jsonify({"ok": True, **load_ui_prefs()})
    return jsonify(load_ui_prefs())


@app.route("/api/played", methods=["POST"])
def api_played():
    data = request.get_json(force=True) or {}
    video_id = data.get("video_id")

    try:
        for cid, pp in list(pending_pick.items()):
            if pp.get("video_id") == video_id:
                pending_pick.pop(cid, None)
                logger.info(f"[PLAYED] confirm canal={cid} video={video_id} -> limpio pending_pick")
    except Exception as e:
        logger.warning(f"[PLAYED] limpiar pending_pick: {e}")

    if not video_id:
        return jsonify({"ok": False, "error": "missing video_id"}), 400

    d = load_plays()
    item = d.get(video_id, {"plays": 0, "last_played": None})
    item["plays"] = int(item.get("plays", 0)) + 1
    item["last_played"] = datetime.now(UTC).isoformat()
    d[video_id] = item
    save_plays(d)

    return jsonify({"ok": True, "video_id": video_id, **item})

@app.route("/splash_video/<path:filename>")
def serve_splash_video(filename):
    return send_from_directory(SPLASH_DIR, filename)

from pathlib import Path

@app.route("/splash")
def splash():
    logger.info(_hdr("HIT /splash"))

    # 1) Intentar usar la elecciÃ³n guardada (si existe)
    splash_path = None
    try:
        cur = Path(CURRENT_SPLASH_FILE)  # CURRENT_SPLASH_FILE viene de settings (str o Path)
        if cur.exists():
            d = json.loads(cur.read_text(encoding="utf-8"))
            # Puede venir como str; normalizamos a Path
            p = d.get("path")
            if p:
                candidate = Path(p)
                if candidate.is_file():
                    splash_path = candidate
    except Exception as e:
        logger.warning(f"[SPLASH] no pude leer CURRENT_SPLASH_FILE: {e}")

    # 2) Fallback: pedir el siguiente splash
    if splash_path is None or not splash_path.is_file():
        nxt = get_next_splash_path()
        # get_next_splash_path puede devolver str o None
        if nxt:
            nxtp = Path(nxt)
            splash_path = nxtp if nxtp.is_file() else None

    # 3) Si seguimos sin splash vÃ¡lido, no romper la vista
    if splash_path is None:
        logger.info("[SPLASH] No hay splash disponible; omitiendo pantalla de splash.")
        # OpciÃ³n A: devolver 204 (sin contenido) y que el front avance
        return "", 204
        # OpciÃ³n B (alternativa): redirigir directo a la TV
        # return redirect(url_for("tv"))

    logger.info(f"[SPLASH] Usando video: {splash_path}")

    # 4) Construir URL del archivo asegurando que tenemos un nombre vÃ¡lido
    filename = splash_path.name  # equivalente a os.path.basename(...)
    return render_template(
        "splash.html",
        video_url=url_for("serve_splash_video", filename=filename),
        tv_url=url_for("tv")
    )



@app.route("/api/clear_intro", methods=["POST"])
def api_clear_intro():
    logger.info(_hdr("POST /api/clear_intro -> borro INTRO_FLAG y avanzo rotaciÃ³n"))
    try:
        _advance_splash_rotation()
    except Exception as e:
        logger.warning(f"[SPLASH] advance error: {e}")

    try:
        if os.path.exists(INTRO_FLAG):
            os.remove(INTRO_FLAG)
    except Exception:
        pass

    try:
        if os.path.exists(CURRENT_SPLASH_FILE):
            os.remove(CURRENT_SPLASH_FILE)
    except Exception:
        pass

    return jsonify({"ok": True})


@app.route("/static-intro.mp4")
def intro_video():
    return send_file(INTRO_PATH, mimetype="video/mp4")


@app.route("/api/boot_probe", methods=["POST", "GET"])
def api_boot_probe():
    # Primer latido apenas carga splash.html (o player.html)
    stage = request.args.get("stage") or "boot"
    _touch_frontend_ping(stage)
    #logger.info(f"[PING] boot_probe stage={stage}")
    return ("ok", 200)

@app.route("/api/kiosk_ping", methods=["GET","POST"])
def api_kiosk_ping():
    src = request.args.get("src", "?")
    ts  = time.time()
    try:
        with open(PING_FILE, "w") as f:
            f.write(f"{ts}|{src}")
    except Exception:
        pass
    #logger.info(f"[PING] {src}")
    return jsonify({"ok": True, "src": src})
 

@app.route("/api/ping", methods=["POST", "GET"])
def api_ping():
    # Heartbeat periÃ³dico desde splash/player
    stage = request.args.get("stage") or "unknown"
    _touch_frontend_ping(stage)
    # DevolvÃ© algo ultra liviano para logs de Chromium si querÃ©s
    return jsonify(ok=True, stage=stage)
    

@app.route("/gestion")
def gestion():
    return render_template("index.html", **_ctx_gestion())

# Alias de compatibilidad: si en algÃºn lado quedÃ³ url_for("index"), redirige a /gestion
@app.route("/index")
def index():
    return redirect(url_for("gestion"))

# (Opcional) atajo cÃ³modo
@app.route("/admin")
def admin():
    return redirect(url_for("gestion"))
    
    
# --- Power control (halt) ---
@app.route("/api/power", methods=["POST"])
def api_power():
    data = request.get_json(force=True) or {}
    action = (data.get("action") or "").lower()
    if action == "halt":
        try:
            # Opcional: log visible
            print("[API] Halt solicitado desde UIâ€¦")
            # Lanza el halt (no bloquea)
            subprocess.Popen(["sudo", "/sbin/shutdown", "-h", "now"])
            return jsonify({"ok": True, "action": "halt"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": False, "error": "unsupported action"}), 400

 
@app.route("/api/gaming", methods=["POST"])
def api_gaming():
    """
    Cambiar entre TVArgenta y modo juegos (RetroPie).

    action:
      - "enter" -> apaga TVArgenta y arranca EmulationStation (via enter-gaming.service)
      - "exit"  -> mata EmulationStation y vuelve a TVArgenta (via return-tvargenta.service)
    """
    data = request.get_json(force=True) or {}
    action = (data.get("action") or "").lower()

    try:
        if action == "enter":
            subprocess.Popen([
                "/usr/bin/sudo",
                "/bin/systemctl",
                "start",
                "enter-gaming.service"
            ])
            return jsonify({"ok": True, "switched": "to_gaming"})

        elif action == "exit":
            subprocess.Popen([
                "/usr/bin/sudo",
                "/bin/systemctl",
                "start",
                "return-tvargenta.service"
            ])
            return jsonify({"ok": True, "switched": "to_tv"})

        else:
            return jsonify({"ok": False, "error": "unsupported action"}), 400

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/bt/ensure", methods=["POST"])
def api_bt_ensure():
    """
    Asegura que el adaptador BT esté encendido y con agente.
    Llamalo cuando entrás al menú Bluetooth en el OSD.
    """
    try:
        bluetooth_manager.ensure_adapter_on()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/bt/paired")
def api_bt_paired():
    try:
        devs = bluetooth_manager.get_paired_devices()
        return jsonify({"ok": True, "devices": devs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/bt/scan")
def api_bt_scan():
    try:
        devs = bluetooth_manager.get_unpaired_devices()
        return jsonify({"ok": True, "devices": devs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/bt/connect", methods=["POST"])
def api_bt_connect():
    data = request.get_json(force=True) or {}
    mac = data.get("mac")
    if not mac:
        return jsonify({"ok": False, "error": "missing mac"}), 400
    res = bluetooth_manager.connect_device(mac)
    return jsonify(res)


@app.route("/api/bt/disconnect", methods=["POST"])
def api_bt_disconnect():
    data = request.get_json(force=True) or {}
    mac = data.get("mac")
    if not mac:
        return jsonify({"ok": False, "error": "missing mac"}), 400
    res = bluetooth_manager.disconnect_device(mac)
    return jsonify(res)


@app.route("/api/bt/forget", methods=["POST"])
def api_bt_forget():
    data = request.get_json(force=True) or {}
    mac = data.get("mac")
    if not mac:
        return jsonify({"ok": False, "error": "missing mac"}), 400
    res = bluetooth_manager.forget_device(mac)
    return jsonify(res)


@app.route("/api/bt/pairconnect", methods=["POST"])
def api_bt_pairconnect():
    data = request.get_json(force=True) or {}
    mac = data.get("mac")
    if not mac:
        return jsonify({"ok": False, "error": "missing mac"}), 400
    res = bluetooth_manager.pair_and_connect(mac)
    return jsonify(res)


# --- API WiFi ---------------------------------------------------------------

@app.route("/wifi_setup")
def wifi_setup():
    return render_template("wifi_setup.html")

@app.get("/api/wifi/status")
def api_wifi_status():
    logger.info("[API][WiFi] /api/wifi/status called")
    try:
        st = wifi_manager.get_status()
        logger.info(f"[API][WiFi] status -> {st}")
        return jsonify({"ok": True, **st})
    except Exception as e:
        logger.error(f"[API][WiFi] status error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/wifi/start_ap")
def api_wifi_start_ap():
    logger.info("[API][WiFi] /api/wifi/start_ap called")
    try:
        res = wifi_manager.start_ap_mode()
        logger.info(f"[API][WiFi] start_ap result: {res}")
        if res.get("ok"):
            _start_ap_auto_stop_timer()
        if not res.get("ok"):
            return jsonify(res), 500
        return jsonify(res)
    except Exception as e:
        logger.error(f"[API][WiFi] start_ap error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/wifi/networks")
def api_wifi_networks():
    logger.info("[API][WiFi] /api/wifi/networks called")
    try:
        nets = wifi_manager.scan_networks()
        logger.info(f"[API][WiFi] networks -> {len(nets)} found")
        return jsonify({"ok": True, "networks": nets})
    except Exception as e:
        logger.error(f"[API][WiFi] networks error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/wifi/connect")
def api_wifi_connect():
    data = request.get_json(force=True) or {}
    ssid = (data.get("ssid") or "").strip()
    password = (data.get("password") or "").strip() or None
    logger.info(f"[API][WiFi] /api/wifi/connect ssid={ssid!r} has_pass={bool(password)}")

    if not ssid:
        return jsonify({"ok": False, "error": "missing_ssid"}), 400

    try:
        res = wifi_manager.connect_with_credentials(ssid, password)
        if res.get("ok"):
            try:
                if _ap_auto_stop_timer and _ap_auto_stop_timer.is_alive():
                    _ap_auto_stop_timer.cancel()
                    logger.info("[WiFi] AP auto-stop timer canceled after successful connect")
            except Exception:
                pass
        logger.info(f"[API][WiFi] connect result: {res}")
    except Exception as e:
        logger.error(f"[API][WiFi] connect error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

    code = 200 if res.get("ok") else 500
    return jsonify(res), code


@app.get("/api/wifi/known")
def api_wifi_known():
    logger.info("[API][WiFi] /api/wifi/known called")
    try:
        nets = wifi_manager.get_known_networks()
        logger.info(f"[API][WiFi] known -> {nets}")
        return jsonify({"ok": True, "networks": nets})
    except Exception as e:
        logger.error(f"[API][WiFi] known error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/wifi/forget")
def api_wifi_forget():
    data = request.get_json(force=True) or {}
    ssid = (data.get("ssid") or "").strip()
    logger.info(f"[API][WiFi] /api/wifi/forget ssid={ssid!r}")
    if not ssid:
        return jsonify({"ok": False, "error": "missing_ssid"}), 400

    try:
        res = wifi_manager.forget_network(ssid)
        logger.info(f"[API][WiFi] forget result: {res}")
    except Exception as e:
        logger.error(f"[API][WiFi] forget error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

    code = 200 if res.get("ok") else 500
    return jsonify(res), code


@app.post("/api/wifi/apply_best")
def api_wifi_apply_best():
    logger.info("[API][WiFi] /api/wifi/apply_best called")
    try:
        res = wifi_manager.choose_best_known_and_connect()
        logger.info(f"[API][WiFi] apply_best result: {res}")
    except Exception as e:
        logger.error(f"[API][WiFi] apply_best error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

    code = 200 if res.get("ok") else 500
    return jsonify(res), code
    
@app.post("/api/wifi/stop_ap")
def api_wifi_stop_ap():
    logger.info("[API][WiFi] /api/wifi/stop_ap called")
    try:
        res = wifi_manager.stop_ap_mode()
        logger.info(f"[API][WiFi] stop_ap result: {res}")
        return jsonify(res)
    except Exception as e:
        logger.error(f"[API][WiFi] stop_ap error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/wifi/qr", methods=["GET"])
def api_wifi_qr():
    target = request.args.get("target", "gestion")

    # 1️⃣ QR para gestión de contenido (solo si hay IP válida)
    if target == "gestion":
        ip = wifi_manager._get_iface_ipv4_addr(wifi_manager.WIFI_IFACE)
        if not ip:
            logger.warning("[WiFi][QR] sin IPv4 asignada -> offline, no genero QR")
            return jsonify({
                "ok": False,
                "offline": True,
                "message": "TVArgenta está funcionando offline. Conectala a WiFi o cable para gestionarla desde otro dispositivo."
            })
        url = f"http://{ip}:5000/gestion"
        qr = wifi_manager._make_qr_data_url(url)
        return jsonify({"ok": True, "url": url, "qr_data": qr})

    # 2️⃣ QR para setup cuando la RPi está en modo AP
    elif target == "ap_url":
        info = wifi_manager._read_json(wifi_manager.AP_STATE_FILE, {})
        ap_ip = info.get("ap_ip") or wifi_manager._get_iface_ipv4_addr(wifi_manager.WIFI_IFACE)
        if not ap_ip:
            return jsonify({
                "ok": False,
                "offline": True,
                "message": "El punto de acceso aún no está listo."
            })
        url = f"http://{ap_ip}:5000/wifi_setup"
        qr = wifi_manager._make_qr_data_url(url)
        return jsonify({"ok": True, "url": url, "qr_data": qr})

    # 3️⃣ Cualquier otro target literal
    else:
        qr = wifi_manager._make_qr_data_url(target)
        return jsonify({"ok": True, "url": target, "qr_data": qr})


# ---[END] API WiFi ---------------------------------------------------------------


# --- API VCR (NFC Mini VHS Tapes) --------------------------------------------

@app.get("/api/vcr/state")
def api_vcr_state():
    """Get current VCR state for frontend."""
    try:
        state = vcr_manager.load_vcr_state()
        # Include rewind progress if rewinding
        if state.get("is_rewinding"):
            progress = vcr_manager.check_rewind_progress()
            state["rewind_progress"] = progress
        return jsonify({"ok": True, **state})
    except Exception as e:
        logger.error(f"[API][VCR] state error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/vcr/pause")
def api_vcr_pause():
    """Toggle pause state."""
    try:
        is_paused = vcr_manager.toggle_pause()
        logger.info(f"[API][VCR] pause toggled -> is_paused={is_paused}")
        return jsonify({"ok": True, "is_paused": is_paused})
    except Exception as e:
        logger.error(f"[API][VCR] pause error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/vcr/rewind")
def api_vcr_rewind():
    """Start the rewind process (2 minutes)."""
    try:
        started = vcr_manager.start_rewind()
        logger.info(f"[API][VCR] rewind started={started}")
        return jsonify({"ok": True, "started": started})
    except Exception as e:
        logger.error(f"[API][VCR] rewind error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# Track last processed trigger mtimes to avoid duplicate processing
_last_pause_trigger_mtime = 0.0
_last_rewind_trigger_mtime = 0.0

@app.get("/api/vcr/check_pause_trigger")
def api_vcr_check_pause_trigger():
    """Check if encoder sent a pause trigger and consume it."""
    global _last_pause_trigger_mtime
    trigger_path = Path("/tmp/trigger_vcr_pause.json")
    try:
        if trigger_path.exists():
            mtime = trigger_path.stat().st_mtime
            if mtime > _last_pause_trigger_mtime:
                _last_pause_trigger_mtime = mtime
                # New trigger - toggle pause
                is_paused = vcr_manager.toggle_pause()
                logger.info(f"[API][VCR] pause trigger consumed -> is_paused={is_paused}")
                return jsonify({"ok": True, "triggered": True, "is_paused": is_paused})
        return jsonify({"ok": True, "triggered": False})
    except Exception as e:
        logger.error(f"[API][VCR] check_pause_trigger error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/vcr/check_rewind_trigger")
def api_vcr_check_rewind_trigger():
    """Check if encoder sent a rewind trigger and consume it."""
    global _last_rewind_trigger_mtime
    trigger_path = Path("/tmp/trigger_vcr_rewind.json")
    try:
        if trigger_path.exists():
            mtime = trigger_path.stat().st_mtime
            if mtime > _last_rewind_trigger_mtime:
                _last_rewind_trigger_mtime = mtime
                # New trigger - start rewind
                started = vcr_manager.start_rewind()
                logger.info(f"[API][VCR] rewind trigger consumed -> started={started}")
                return jsonify({"ok": True, "triggered": True, "started": started})
        return jsonify({"ok": True, "triggered": False})
    except Exception as e:
        logger.error(f"[API][VCR] check_rewind_trigger error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/vcr/seek")
def api_vcr_seek():
    """Seek to a specific position (for admin/debug)."""
    data = request.get_json(force=True) or {}
    position = data.get("position_sec", 0)
    try:
        actual_pos = vcr_manager.seek_to_position(float(position))
        logger.info(f"[API][VCR] seek to {position} -> actual={actual_pos}")
        return jsonify({"ok": True, "position_sec": actual_pos})
    except Exception as e:
        logger.error(f"[API][VCR] seek error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/vcr/tapes")
def api_vcr_tapes():
    """List all registered tapes."""
    try:
        tapes = vcr_manager.get_all_tapes()
        # Enrich with video metadata
        for tape in tapes:
            video_info = vcr_manager.get_video_info(tape.get("video_id", ""))
            if video_info:
                tape["video_title"] = video_info.get("title", tape.get("video_id"))
                tape["video_duration"] = video_info.get("duracion", 0)
        return jsonify({"ok": True, "tapes": tapes})
    except Exception as e:
        logger.error(f"[API][VCR] tapes list error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/vcr/tapes/register")
def api_vcr_tapes_register():
    """Register a new tape (map NFC UID to video)."""
    data = request.get_json(force=True) or {}
    uid = (data.get("uid") or "").strip()
    video_id = (data.get("video_id") or "").strip()
    title = data.get("title")  # Optional, will be fetched from metadata if not provided

    if not uid:
        return jsonify({"ok": False, "error": "missing_uid"}), 400
    if not video_id:
        return jsonify({"ok": False, "error": "missing_video_id"}), 400

    try:
        tape = vcr_manager.register_tape(uid, video_id, title)
        logger.info(f"[API][VCR] tape registered: uid={uid} video={video_id}")

        # Check if this tape is currently inserted (unknown_tape_uid matches)
        # If so, auto-transition to playback mode
        state = vcr_manager.load_vcr_state()
        if state.get("unknown_tape_uid") == uid:
            # Get video duration and title for playback
            duration = vcr_manager.get_video_duration(video_id)
            video_title = tape.get("title", video_id)
            position = vcr_manager.get_tape_position(uid)

            # Transition to tape inserted state - this will trigger playback
            vcr_manager.set_tape_inserted(uid, video_id, video_title, duration, position)
            logger.info(f"[API][VCR] auto-started playback for newly registered tape: {uid}")

        return jsonify({"ok": True, "tape": tape})
    except Exception as e:
        logger.error(f"[API][VCR] tape register error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.delete("/api/vcr/tapes/<uid>")
def api_vcr_tapes_delete(uid):
    """Remove a tape mapping."""
    try:
        # URL decode the UID (colons may be encoded)
        uid = urllib.parse.unquote(uid)
        removed = vcr_manager.unregister_tape(uid)
        logger.info(f"[API][VCR] tape deleted: uid={uid} removed={removed}")
        return jsonify({"ok": True, "removed": removed})
    except Exception as e:
        logger.error(f"[API][VCR] tape delete error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/vcr/tapes/scan")
def api_vcr_tapes_scan():
    """Get currently detected but unregistered tape (for admin registration)."""
    try:
        state = vcr_manager.load_vcr_state()
        unknown_uid = state.get("unknown_tape_uid")
        if unknown_uid:
            return jsonify({"ok": True, "detected": True, "uid": unknown_uid})
        return jsonify({"ok": True, "detected": False, "uid": None})
    except Exception as e:
        logger.error(f"[API][VCR] tape scan error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/vcr/trigger")
def api_vcr_trigger():
    """Check if VCR state has changed (for frontend polling)."""
    try:
        if VCR_TRIGGER_FILE.exists():
            mtime = VCR_TRIGGER_FILE.stat().st_mtime
            return jsonify({"ok": True, "mtime": mtime})
        return jsonify({"ok": True, "mtime": 0})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/vcr/countdown_trigger")
def api_vcr_countdown_trigger():
    """Get countdown value for rewind (from encoder button hold)."""
    from settings import VCR_COUNTDOWN_TRIGGER
    try:
        if VCR_COUNTDOWN_TRIGGER.exists():
            with open(VCR_COUNTDOWN_TRIGGER, "r") as f:
                data = json.load(f)
            return jsonify({"ok": True, "countdown": data.get("countdown")})
        return jsonify({"ok": True, "countdown": None})
    except Exception as e:
        return jsonify({"ok": False, "countdown": None, "error": str(e)}), 500


@app.get("/api/vcr/videos")
def api_vcr_videos():
    """Get list of videos available for tape registration."""
    try:
        metadata = vcr_manager._read_json(METADATA_FILE, {})
        videos = []
        seen_ids = set()

        # First, add videos with metadata
        for video_id, info in metadata.items():
            videos.append({
                "video_id": video_id,
                "title": info.get("title", video_id),
                "duration": info.get("duracion", 0),
            })
            seen_ids.add(video_id)

        # Then, add videos from filesystem that don't have metadata
        if os.path.isdir(VIDEO_DIR):
            for filename in os.listdir(VIDEO_DIR):
                if filename.endswith(".mp4"):
                    video_id = os.path.splitext(filename)[0]
                    if video_id not in seen_ids:
                        videos.append({
                            "video_id": video_id,
                            "title": f"{video_id} (no metadata)",
                            "duration": 0,
                        })

        # Sort by title
        videos.sort(key=lambda v: v.get("title", "").lower())
        return jsonify({"ok": True, "videos": videos})
    except Exception as e:
        logger.error(f"[API][VCR] videos list error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/vcr/empty_tape_qr")
def api_vcr_empty_tape_qr():
    """
    Generate QR code for VCR recording page when an empty tape is inserted.
    Returns WiFi status, SSID, and QR code for the recording page.
    Tries mDNS hostname first, falls back to IP address.
    """
    try:
        # Get WiFi status
        wifi_status = wifi_manager.get_status()
        mode = wifi_status.get("mode", "disconnected")
        ssid = wifi_status.get("ssid", "")

        # If not connected to WiFi, return status without QR
        if mode != "client" or not ssid:
            return jsonify({
                "ok": True,
                "wifi_connected": False,
                "mode": mode,
                "ssid": None,
                "qr_data": None,
                "url": None,
            })

        # Try mDNS hostname first
        try:
            hostname = socket.gethostname()
            mdns_hostname = f"{hostname}.local"
            # Verify mDNS is resolvable (quick check)
            socket.gethostbyname(mdns_hostname)
            url = f"http://{mdns_hostname}:5000/vcr_record"
        except (socket.gaierror, OSError):
            # mDNS not available, fall back to IP
            ip = wifi_manager._get_iface_ipv4_addr(wifi_manager.WIFI_IFACE)
            if not ip:
                return jsonify({
                    "ok": True,
                    "wifi_connected": True,
                    "mode": mode,
                    "ssid": ssid,
                    "qr_data": None,
                    "url": None,
                    "error": "No IP address available",
                })
            url = f"http://{ip}:5000/vcr_record"

        # Generate QR code
        qr_data = wifi_manager._make_qr_data_url(url)

        return jsonify({
            "ok": True,
            "wifi_connected": True,
            "mode": mode,
            "ssid": ssid,
            "qr_data": qr_data,
            "url": url,
        })

    except Exception as e:
        logger.error(f"[API][VCR] empty_tape_qr error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/vcr_admin")
def vcr_admin():
    """VCR tape management admin page."""
    return render_template("vcr_admin.html")


# --- VCR Recording (Upload) ---------------------------------------------------

# Max upload size: 3GB
VCR_MAX_UPLOAD_SIZE = 3 * 1024 * 1024 * 1024  # 3GB in bytes


def _vcr_recording_state_write(state: dict) -> None:
    """Write VCR recording state atomically."""
    tmp_path = VCR_RECORDING_STATE_FILE.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f)
    tmp_path.replace(VCR_RECORDING_STATE_FILE)


def _vcr_recording_state_read() -> dict:
    """Read VCR recording state."""
    if not VCR_RECORDING_STATE_FILE.exists():
        return {"recording": False}
    try:
        with open(VCR_RECORDING_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"recording": False}


def _vcr_recording_state_clear() -> None:
    """Clear VCR recording state."""
    if VCR_RECORDING_STATE_FILE.exists():
        VCR_RECORDING_STATE_FILE.unlink()


@app.get("/vcr_record")
def vcr_record():
    """VCR recording page for uploading videos to empty tapes."""
    # Get the tape UID from the current VCR state
    state = vcr_manager.load_vcr_state()
    tape_uid = state.get("unknown_tape_uid", "")
    return render_template("vcr_record.html", tape_uid=tape_uid)


@app.get("/api/vcr/record/progress")
def api_vcr_record_progress():
    """Get current recording progress for polling."""
    try:
        state = _vcr_recording_state_read()
        # Log occasionally to avoid spam (only when recording is active)
        if state.get("recording"):
            logger.debug(f"[VCR] /progress: recording={state.get('recording')}, status={state.get('status')}, progress={state.get('progress')}")
        return jsonify({"ok": True, **state})
    except Exception as e:
        logger.error(f"[API][VCR] record progress error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/vcr/record/start")
def api_vcr_record_start():
    """
    Initialize recording state before upload begins.
    Called by the client before starting the actual file upload.
    """
    data = request.get_json(force=True) or {}
    tape_uid = (data.get("tape_uid") or "").strip()
    filename = data.get("filename", "video")
    file_size = data.get("file_size", 0)

    logger.info(f"[VCR] /start called: tape_uid={tape_uid}, filename={filename}, file_size={file_size}")

    if not tape_uid:
        logger.warning("[VCR] /start failed: missing_tape_uid")
        return jsonify({"ok": False, "error": "missing_tape_uid"}), 400

    # Check if tape is still inserted
    vcr_state = vcr_manager.load_vcr_state()
    logger.info(f"[VCR] /start: VCR state unknown_tape_uid={vcr_state.get('unknown_tape_uid')}")
    if vcr_state.get("unknown_tape_uid") != tape_uid:
        logger.warning(f"[VCR] /start failed: tape_not_inserted (expected {tape_uid}, got {vcr_state.get('unknown_tape_uid')})")
        return jsonify({"ok": False, "error": "tape_not_inserted"}), 400

    try:
        # Generate video ID
        safe_filename = secure_filename(filename)
        video_id = os.path.splitext(safe_filename)[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        video_id = f"{video_id}_{timestamp}"

        # Initialize recording state
        state_to_write = {
            "recording": True,
            "tape_uid": tape_uid,
            "video_id": video_id,
            "progress": 0,
            "total_bytes": file_size,
            "received_bytes": 0,
            "status": "recording",
            "error": None,
        }
        logger.info(f"[VCR] /start: Writing recording state to {VCR_RECORDING_STATE_FILE}")
        _vcr_recording_state_write(state_to_write)

        # Verify the state was written
        verify_state = _vcr_recording_state_read()
        logger.info(f"[VCR] /start: Verified recording state: recording={verify_state.get('recording')}, status={verify_state.get('status')}")

        # Trigger VCR state update so TV shows recording screen
        vcr_manager.trigger_vcr_update()

        logger.info(f"[VCR] Recording initialized: tape={tape_uid} video={video_id}")

        return jsonify({"ok": True, "video_id": video_id})

    except Exception as e:
        logger.error(f"[VCR] Recording start error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/vcr/record/client_progress")
def api_vcr_record_client_progress():
    """
    Receive progress updates from the uploading client.
    This allows the TV to show real-time progress during network upload.
    """
    data = request.get_json(force=True) or {}
    progress = data.get("progress", 0)
    received_bytes = data.get("received_bytes", 0)
    total_bytes = data.get("total_bytes", 0)
    status = data.get("status", "recording")  # 'recording' or 'processing'

    try:
        state = _vcr_recording_state_read()
        if not state.get("recording"):
            logger.warning(f"[VCR] /client_progress: not recording (state={state})")
            return jsonify({"ok": False, "error": "not_recording"}), 400

        # Update progress from client
        state["progress"] = progress
        state["received_bytes"] = received_bytes
        state["status"] = status
        if total_bytes:
            state["total_bytes"] = total_bytes

        _vcr_recording_state_write(state)
        logger.debug(f"[VCR] /client_progress: updated to {progress}% status={status}")

        return jsonify({"ok": True})

    except Exception as e:
        logger.error(f"[VCR] Client progress error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/vcr/record/upload")
def api_vcr_record_upload():
    """
    Upload a video file for VCR recording.
    Stores the file as-is (no transcoding) and auto-registers the tape.
    Note: Progress is tracked by the client via /api/vcr/record/client_progress.
    """
    tape_uid = request.form.get("tape_uid", "").strip()
    logger.info(f"[VCR] /upload: Flask finished receiving file, tape_uid={tape_uid}")

    if not tape_uid:
        return jsonify({"ok": False, "error": "missing_tape_uid"}), 400

    # Get recording state if available (set by /start for progress tracking)
    # But don't require it - upload should work even if /start wasn't called
    recording_state = _vcr_recording_state_read()
    logger.info(f"[VCR] /upload: recording_state={recording_state}")
    video_id_from_state = None
    if recording_state.get("recording") and recording_state.get("tape_uid") == tape_uid:
        video_id_from_state = recording_state.get("video_id")
        logger.info(f"[VCR] /upload: Using video_id from state: {video_id_from_state}")

    # Check if tape is still inserted
    vcr_state = vcr_manager.load_vcr_state()
    if vcr_state.get("unknown_tape_uid") != tape_uid:
        _vcr_recording_state_write({
            "recording": False,
            "status": "failed",
            "error": "tape_removed",
        })
        return jsonify({"ok": False, "error": "tape_not_inserted"}), 400

    if "video" not in request.files:
        return jsonify({"ok": False, "error": "no_file"}), 400

    file = request.files["video"]
    if not file.filename:
        return jsonify({"ok": False, "error": "empty_filename"}), 400

    if not file.filename.lower().endswith(".mp4"):
        _vcr_recording_state_write({
            "recording": False,
            "status": "failed",
            "error": "invalid_format",
        })
        return jsonify({"ok": False, "error": "invalid_format", "message": "Only .mp4 files allowed"}), 400

    try:
        # Get video_id from recording state if available, otherwise generate
        video_id = video_id_from_state
        if not video_id:
            filename = secure_filename(file.filename)
            video_id = os.path.splitext(filename)[0]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            video_id = f"{video_id}_{timestamp}"

        final_path = os.path.join(VIDEO_DIR, f"{video_id}.mp4")

        # Ensure video directory exists
        os.makedirs(VIDEO_DIR, exist_ok=True)

        logger.info(f"[VCR] Receiving upload: tape={tape_uid} video={video_id}")

        # Save the file (Flask has already buffered it)
        file.save(final_path)

        # Verify file size
        file_size = os.path.getsize(final_path)
        if file_size > VCR_MAX_UPLOAD_SIZE:
            os.unlink(final_path)
            _vcr_recording_state_write({
                "recording": False,
                "status": "failed",
                "error": "file_too_large",
            })
            vcr_manager.trigger_vcr_update()
            return jsonify({"ok": False, "error": "file_too_large", "message": "Max file size is 3GB"}), 400

        # Get video duration using ffprobe
        try:
            duration = get_video_duration(final_path)
        except Exception:
            duration = 0

        # Update metadata
        metadata = vcr_manager._read_json(METADATA_FILE, {})
        metadata[video_id] = {
            "title": video_id,
            "duracion": duration,
        }
        vcr_manager._write_json_atomic(METADATA_FILE, metadata)

        # Register the tape with the video
        tape = vcr_manager.register_tape(tape_uid, video_id, video_id)
        logger.info(f"[VCR] Recording complete: tape={tape_uid} video={video_id}")

        # Update recording state to complete
        _vcr_recording_state_write({
            "recording": False,
            "status": "complete",
            "tape_uid": tape_uid,
            "video_id": video_id,
            "progress": 100,
            "error": None,
        })

        # Auto-start playback (same as tape registration)
        position = vcr_manager.get_tape_position(tape_uid)
        vcr_manager.set_tape_inserted(tape_uid, video_id, video_id, duration, position)
        logger.info(f"[VCR] Auto-started playback for recorded tape: {tape_uid}")

        return jsonify({
            "ok": True,
            "video_id": video_id,
            "tape_uid": tape_uid,
            "duration": duration,
        })

    except Exception as e:
        logger.error(f"[VCR] Recording failed: {e}")
        _vcr_recording_state_write({
            "recording": False,
            "status": "failed",
            "error": str(e),
        })
        vcr_manager.trigger_vcr_update()
        return jsonify({"ok": False, "error": str(e)}), 500


# --- VCR Background Position Tracker -----------------------------------------

_vcr_tracker_running = False


def _vcr_position_tracker():
    """Background thread to increment tape position and check rewind completion."""
    global _vcr_tracker_running
    _vcr_tracker_running = True
    logger.info("[VCR] Position tracker thread started")

    while _vcr_tracker_running:
        try:
            state = vcr_manager.load_vcr_state()

            if state.get("tape_inserted"):
                if state.get("is_rewinding"):
                    # Check if rewind is complete
                    progress = vcr_manager.check_rewind_progress()
                    if progress.get("complete"):
                        vcr_manager.complete_rewind()
                        logger.info("[VCR] Rewind complete")

                elif not state.get("is_paused"):
                    # Tape is playing - increment position
                    vcr_manager.increment_position(1.0)

                    # Periodically persist position to disk
                    if vcr_manager.should_persist_position():
                        vcr_manager.persist_current_position()

        except Exception as e:
            logger.error(f"[VCR] Position tracker error: {e}")

        time.sleep(1)

    logger.info("[VCR] Position tracker thread stopped")


def _start_vcr_tracker():
    """Start the VCR position tracker thread."""
    tracker_thread = threading.Thread(target=_vcr_position_tracker, daemon=True)
    tracker_thread.start()
    return tracker_thread


# ---[END] API VCR ------------------------------------------------------------


@app.before_request
def _i18n_before_request():
    cfg = load_config_i18n()
    lang = cfg.get("language", "es")

    # Override por querystring (?lang=en) para pruebas
    lang = request.args.get("lang", lang)

    # Base global (es.json, en.json, de.json)
    translations = load_translations(lang)

    # Mapear endpoints Flask -> nombre base de JSON de página
    endpoint_to_page = {
        # Dashboard / gestión
        "gestion": "index",
        "index": "index",

        # Tags
        "tags": "tags",
        
         # Configuración
        "configuracion": "configuracion",
        
         # Upload
        "upload": "upload",
        
         # Canales
        "canales": "canales",
        "editar_canal": "canales",
        "guardar_canal": "canales",
        "eliminar_canal": "canales",
        
         # modo tele
        "vertele": "vertele",
        
        "wifi_setup": "wifi_setup"

        # Si después querés i18n por página:
        # "canales": "canales",
        # "configuracion": "configuracion",
        # "upload": "upload",
        # etc.
    }

    page = endpoint_to_page.get(request.endpoint)

    if page:
        page_trans = load_page_translations(lang, page)
        if page_trans:
            translations.update(page_trans)
            logger.info(
                f"[I18N] Merge page={page}_{lang}.json -> total_claves={len(translations)}"
            )
        else:
            logger.info(f"[I18N] Sin i18n específica para page={page}, lang={lang}")

    g.lang = lang
    g.translations = translations




def tr(key: str):
    """Traducción directa desde el diccionario cargado."""
    return g.translations.get(key, key)

@app.context_processor
def inject_i18n():
    """Inyecta funciones y variables en todos los templates."""
    return dict(tr=tr, lang=g.lang, translations=g.translations)
 
@app.get("/api/lang")
def api_lang():
    """Devuelve el idioma actual (para JS en player.html)"""
    return jsonify({"lang": g.lang})
    
@app.get("/i18n/<page>_<lang>.json")
def serve_page_i18n(page, lang):
    """
    Devuelve el JSON específico de una página, p.ej. /i18n/index_es.json.
    Útil para frontends que cargan textos vía fetch.
    """
    page_file = I18N_DIR / f"{page}_{lang}.json"
    if not page_file.exists():
        return jsonify({}), 404

    try:
        with page_file.open("r", encoding="utf-8") as f:
            return jsonify(json.load(f) or {})
    except Exception as e:
        logger.error(f"[I18N] Error sirviendo {page_file}: {e}")
        return jsonify({}), 500


@app.get("/i18n/<lang>.json")
def serve_i18n(lang):
    """Devuelve el diccionario de traducciones (para fallback JS)"""
    return jsonify(load_translations(lang))

@app.post("/api/language")
def api_language_set():
    """Cambia el idioma actual desde la UI de la tele"""
    data = request.get_json(force=True) or {}
    lang = data.get("lang")
    logger.info(f"[I18N] POST /api/language recibido con lang={lang!r}")
    if lang not in ("es", "en", "de"):
        logger.warning(f"[I18N] Idioma no soportado: {lang}")
        return jsonify({"ok": False, "error": "Idioma no soportado"}), 400

    cfg = load_config_i18n()
    prev = cfg.get("language")
    cfg["language"] = lang
    save_config_i18n(cfg)
    logger.info(
        f"[I18N] Idioma actualizado {prev!r} → {lang!r} en {CONFIG_PATH}"
    )
    return jsonify({"ok": True, "lang": lang})


 
if __name__ == "__main__":
    encoder_path = str(Path(APP_DIR, "tvargenta_encoder.py"))
    
    # Asegurarse de que no quede flag viejo de kiosk
    try:
        os.remove("/tmp/tvargenta_kiosk_launched")
    except FileNotFoundError:
        pass

    # Clear any stale VCR state from previous session
    vcr_manager.clear_stale_vcr_state()

    #  Asegurarse de que NO quede ningÃºn encoder viejo corriendo
    try:
        subprocess.run(["pkill", "-f", "encoder_reader"], check=False)
        time.sleep(0.2)
    except Exception as e:
        print(f"[APP] Aviso: no pude matar encoders previos: {e}")

    # Lanzar encoder limpio
    try:
        encoder_proc = subprocess.Popen(["python3", encoder_path], start_new_session=True)
    except Exception as e:
        print(f"[APP] No se pudo lanzar el encoder: {e}")
        encoder_proc = None

    # Lanzar NFC reader daemon para VCR
    nfc_reader_path = str(Path(APP_DIR, "nfc_reader.py"))
    nfc_proc = None
    try:
        # Kill any existing NFC reader process
        subprocess.run(["pkill", "-f", "nfc_reader.py"], check=False)
        time.sleep(0.2)
        nfc_proc = subprocess.Popen(["python3", nfc_reader_path], start_new_session=True)
        print("[APP] NFC reader daemon launched")
    except Exception as e:
        print(f"[APP] No se pudo lanzar el NFC reader: {e}")

    # Start VCR position tracker thread
    _start_vcr_tracker()
    print("[APP] VCR position tracker started")

    # Initialize broadcast TV scheduler
    try:
        scheduler.initialize_scheduler()
        print("[APP] Broadcast TV scheduler initialized")
    except Exception as e:
        print(f"[APP] Warning: Could not initialize scheduler: {e}")

    def cleanup():
        if encoder_proc:
            print("[APP] Terminando proceso del encoder...")
            encoder_proc.terminate()
        if nfc_proc:
            print("[APP] Terminando proceso del NFC reader...")
            nfc_proc.terminate()

    atexit.register(cleanup)

    os.makedirs(VIDEO_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)
    
    init_volumen_por_defecto()

    # Lanzar Chromium una sola vez en background
    threading.Thread(target=launch_kiosk_once, daemon=True).start()
    
    def _read_ping():
        try:
            with open(PING_FILE, "r") as f:
                s = f.read().strip()
            parts = s.split("|", 1)
            return float(parts[0]), (parts[1] if len(parts) > 1 else "?")
        except Exception:
            return 0.0, "?"

    def kiosk_watchdog(timeout_first=65, retry_url="http://localhost:5000/"):
        global _last_frontend_ping, _last_frontend_stage, _watchdog_already_retry
        start = time.monotonic()

        # Espera ping real del frontend
        while (time.monotonic() - start) < timeout_first:
            if _last_frontend_ping and (time.monotonic() - _last_frontend_ping) < timeout_first:
                logger.info(f"[WD] Frontend OK (stage={_last_frontend_stage}) en {(time.monotonic()-start):.1f}s")
                return
            time.sleep(0.5)

        if _watchdog_already_retry:
            logger.warning("[WD] Sin ping y ya se reintentÃ³ antes. No relanzo mÃ¡s.")
            return

        logger.warning("[WD] No hubo ping de splash/player a tiempo. Reintentando Chromium una vez...")
        try:
            subprocess.run(["pkill", "-f", "chromium"], check=False)
            time.sleep(0.7)
        except Exception:
            pass

        _watchdog_already_retry = True
        restart_kiosk(url=retry_url)
      
    
    threading.Thread(target=kiosk_watchdog, daemon=True).start()

    app.run(debug=False, host="0.0.0.0")
