#!/usr/bin/env python3
import os
import subprocess
import time
import json
from pathlib import Path
from player_utils import cambiar_canal

CANAL_JSON_PATH = "/srv/tvargenta/content/canales.json"
CANAL_ACTIVO_PATH = "/srv/tvargenta/content/canal_activo.json"

# --- Nuevo: trigger para menu (front hace polling de mtime) ---
MENU_TRIGGER_PATH = "/tmp/trigger_menu.json"
MENU_STATE_PATH  = "/tmp/menu_state.json"
MENU_NAV_PATH    = "/tmp/trigger_menu_nav.json"
MENU_SELECT_PATH = "/tmp/trigger_menu_select.json"

# --- VCR (NFC Mini VHS) paths ---
VCR_STATE_PATH = "/tmp/vcr_state.json"
VCR_PAUSE_TRIGGER = "/tmp/trigger_vcr_pause.json"
VCR_REWIND_TRIGGER = "/tmp/trigger_vcr_rewind.json"
VCR_COUNTDOWN_TRIGGER = "/tmp/trigger_vcr_countdown.json"
VCR_CHANNEL_ID = "03"  # Channel 3 is VCR input
VCR_TAP_THRESHOLD = 0.4  # Seconds - releases before this are "tap" (pause/play)
VCR_REWIND_HOLD_SECONDS = 3.0  # Hold button for 3 seconds to start rewind

estado = "idle"          # idle | evaluando | volume | vcr_hold
hubo_giro = False
ultimo_estado = "idle"
last_volume_activity = 0.0

# VCR-specific state
vcr_btn_press_time = 0.0  # When button was pressed on VCR channel
vcr_countdown_active = False  # True once we've passed tap threshold and shown countdown

DEFAULT_VOL = 25  # porcentaje


def ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def get_canal_actual():
    if Path(CANAL_ACTIVO_PATH).exists():
        with open(CANAL_ACTIVO_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("canal_id", "default")
    return "default"

def get_lista_canales():
    """Get list of channels with system channel 03 always at the beginning."""
    with open(CANAL_JSON_PATH, "r", encoding="utf-8") as f:
        user_channels = list(json.load(f).keys())
    # Remove 03 if it somehow exists in user channels (shouldn't, but safety)
    user_channels = [c for c in user_channels if c != "03"]
    # Always inject 03 (AV input) at the beginning
    return ["03"] + user_channels

def cambiar_al_siguiente(delta):
    canales = get_lista_canales()
    actual = get_canal_actual()
    try:
        idx = canales.index(actual)
    except ValueError:
        idx = 0
    nuevo_idx = (idx + delta) % len(canales)
    nuevo_id = canales[nuevo_idx]

    if nuevo_id != actual:
        print(f"[{ts()}] [ENCODER] Canal cambiado a: {nuevo_id}")
        cambiar_canal(nuevo_id, resetear_cola=True)

        # Notificar al frontend para que recargue
        with open("/tmp/trigger_reload.json", "w") as f:
            json.dump({"timestamp": time.time()}, f)
    else:
        print(f"[{ts()}] [ENCODER] Canal no cambiÃ³ (circular)")

def ajustar_volumen(delta):
    path = "/tmp/tvargenta_volumen.json"
    valor = DEFAULT_VOL

    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                valor = json.load(f).get("valor", DEFAULT_VOL)
        except Exception:
            valor = DEFAULT_VOL  # por si el JSON se dañó

    nuevo_valor = max(0, min(100, valor + delta))

    # Guardar y notificar si cambió o si el archivo no existía
    if (not os.path.exists(path)) or (nuevo_valor != valor):
        with open(path, "w") as f:
            json.dump({"valor": nuevo_valor}, f)

        with open("/tmp/trigger_volumen.json", "w") as f:
            json.dump({"timestamp": time.time()}, f)

    print(f"[{ts()}] [VOLUMEN] Ajustado a: {nuevo_valor}")

# --- Nuevo: tocar archivo para abrir/cerrar menÃº (flanco de bajada sin giro) ---
def trigger_menu():
    try:
        with open(MENU_TRIGGER_PATH, "w") as f:
            json.dump({"timestamp": time.time()}, f)
        print(f"[{ts()}] [MENU] Trigger emitido ({MENU_TRIGGER_PATH})")
    except Exception as e:
        print(f"[{ts()}] [MENU] Error al emitir trigger: {e}")

def menu_is_open():
    if Path(MENU_STATE_PATH).exists():
        try:
            with open(MENU_STATE_PATH, "r") as f:
                data = json.load(f)
            return bool(data.get("open", False))
        except Exception:
            return False
    return False

def trigger_menu_nav(delta):
    try:
        with open(MENU_NAV_PATH, "w") as f:
            json.dump({"delta": int(delta), "timestamp": time.time()}, f)
        print(f"[{ts()}] [MENU] NAV delta={delta}")
    except Exception as e:
        print(f"[{ts()}] [MENU] Error NAV: {e}")

def trigger_menu_select():
    try:
        with open(MENU_SELECT_PATH, "w") as f:
            json.dump({"timestamp": time.time()}, f)
        print(f"[{ts()}] [MENU] SELECT")
    except Exception as e:
        print(f"[{ts()}] [MENU] Error SELECT: {e}")

def trigger_next_video():
    # Tocar el trigger de reload para que el front pida /api/next_video
    try:
        with open("/tmp/trigger_reload.json", "w") as f:
            json.dump({"timestamp": time.time(), "reason": "BTN_NEXT"}, f)
        print(f"[{ts()}] [NEXT] Trigger next video")
    except Exception as e:
        print(f"[{ts()}] [NEXT] Error al disparar next: {e}")


# --- VCR (NFC Mini VHS) functions ---

def is_vcr_channel():
    """Check if current channel is the VCR input (Channel 03)."""
    return get_canal_actual() == VCR_CHANNEL_ID


def get_vcr_state():
    """Load current VCR state from temp file."""
    if Path(VCR_STATE_PATH).exists():
        try:
            with open(VCR_STATE_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def vcr_has_tape():
    """Check if a tape is currently inserted in VCR."""
    state = get_vcr_state()
    return state.get("tape_inserted", False)


def vcr_is_paused():
    """Check if VCR is currently paused."""
    state = get_vcr_state()
    return state.get("is_paused", False)


def vcr_is_rewinding():
    """Check if VCR is currently rewinding."""
    state = get_vcr_state()
    return state.get("is_rewinding", False)


def trigger_vcr_pause():
    """Trigger VCR pause/play toggle."""
    try:
        with open(VCR_PAUSE_TRIGGER, "w") as f:
            json.dump({"timestamp": time.time()}, f)
        print(f"[{ts()}] [VCR] Pause/Play triggered")
    except Exception as e:
        print(f"[{ts()}] [VCR] Error triggering pause: {e}")


def trigger_vcr_rewind():
    """Trigger VCR rewind start."""
    try:
        with open(VCR_REWIND_TRIGGER, "w") as f:
            json.dump({"timestamp": time.time()}, f)
        print(f"[{ts()}] [VCR] Rewind triggered")
    except Exception as e:
        print(f"[{ts()}] [VCR] Error triggering rewind: {e}")


def trigger_vcr_countdown(seconds_remaining):
    """
    Update VCR countdown display.
    seconds_remaining: Number to show (3, 2, 1) or None to hide countdown.
    """
    try:
        with open(VCR_COUNTDOWN_TRIGGER, "w") as f:
            json.dump({
                "countdown": seconds_remaining,
                "timestamp": time.time()
            }, f)
        if seconds_remaining is not None:
            print(f"[{ts()}] [VCR] Countdown: {seconds_remaining}")
        else:
            print(f"[{ts()}] [VCR] Countdown cancelled")
    except Exception as e:
        print(f"[{ts()}] [VCR] Error updating countdown: {e}")

if __name__ == "__main__":
    import select

    print(f"[{ts()}] [ENCODER] Escuchando salida de ./encoder_reader")
    proc = subprocess.Popen(["./encoder_reader"], stdout=subprocess.PIPE, text=True)

    last_countdown_value = None  # Track last countdown to avoid spamming

    try:
        while True:
            # Use select with timeout to allow watchdog checks even without input
            ready, _, _ = select.select([proc.stdout], [], [], 0.2)

            # --- watchdog de volumen ---
            if estado == "volume" and last_volume_activity and (time.time() - last_volume_activity) > 3.2:
                estado = ultimo_estado
                last_volume_activity = 0.0
                print(f"[{ts()}] [ENCODER] Volume timeout -> volvemos a {estado}")

            # --- VCR hold watchdog: update countdown while button held ---
            if estado == "vcr_hold" and vcr_btn_press_time > 0:
                elapsed = time.time() - vcr_btn_press_time

                if elapsed >= VCR_REWIND_HOLD_SECONDS:
                    # Held long enough - trigger rewind
                    trigger_vcr_rewind()
                    trigger_vcr_countdown(None)  # Clear countdown
                    vcr_btn_press_time = 0.0
                    vcr_countdown_active = False
                    last_countdown_value = None
                    estado = "idle"
                    print(f"[{ts()}] [VCR] Rewind initiated after {VCR_REWIND_HOLD_SECONDS}s hold")

                elif elapsed >= VCR_TAP_THRESHOLD:
                    # Past tap threshold - this is a hold, show/update countdown
                    if not vcr_countdown_active:
                        # First time past threshold - activate countdown mode
                        vcr_countdown_active = True
                        print(f"[{ts()}] [VCR] Hold detected, starting countdown")

                    # Calculate remaining time for rewind (countdown from threshold point)
                    # Time remaining = REWIND_HOLD - elapsed
                    remaining = int(VCR_REWIND_HOLD_SECONDS - elapsed) + 1
                    if remaining != last_countdown_value and remaining > 0:
                        trigger_vcr_countdown(remaining)
                        last_countdown_value = remaining
                # else: still in tap window, don't show anything yet

            # Process input if available
            if not ready:
                continue

            raw = proc.stdout.readline()
            if not raw:  # EOF - process ended
                break

            line = raw.strip()
            if not line:
                continue

            print(f"[{ts()}] [DEBUG] Evento recibido: {line}")

            # --- Giro del encoder ---
            if line.startswith("ROTARY_"):
                if estado == "idle":
                    if menu_is_open():
                        delta = +1 if line == "ROTARY_CW" else -1
                        trigger_menu_nav(delta)
                    else:
                        # Giro sin apretar: zapping de canales
                        print(f"[{ts()}] [ENCODER] Gesto = cambio de canal")
                        delta = +1 if line == "ROTARY_CW" else -1
                        cambiar_al_siguiente(delta)

                elif estado == "evaluando":
                    # Se estaba apretando: si gira, esto es volumen
                    estado = "volume"
                    hubo_giro = True
                    last_volume_activity = time.time()
                    print(f"[{ts()}] [ENCODER] Gesto = volumen (entrando a modo volume)")

                elif estado == "volume":
                    # Ajuste fino del volumen
                    delta = +5 if line == "ROTARY_CW" else -5
                    ajustar_volumen(delta)
                    last_volume_activity = time.time()

                elif estado == "vcr_hold":
                    # Rotation while holding on VCR - cancel rewind countdown, switch to volume
                    trigger_vcr_countdown(None)
                    vcr_btn_press_time = 0.0
                    vcr_countdown_active = False
                    estado = "volume"
                    hubo_giro = True
                    last_volume_activity = time.time()
                    print(f"[{ts()}] [VCR] Hold cancelled, switching to volume mode")

            # --- Boton: flanco ascendente (apreto) ---
            elif line == "BTN_PRESS":
                if estado == "idle":
                    # Check if we're on VCR channel with tape inserted
                    if is_vcr_channel() and vcr_has_tape() and not vcr_is_rewinding():
                        # VCR mode: start tracking hold time
                        # Don't show countdown yet - wait for TAP_THRESHOLD to distinguish tap from hold
                        estado = "vcr_hold"
                        vcr_btn_press_time = time.time()
                        vcr_countdown_active = False  # Will become True after TAP_THRESHOLD
                        hubo_giro = False
                        print(f"[{ts()}] [VCR] Button pressed, waiting to distinguish tap/hold")
                    elif is_vcr_channel():
                        # On VCR channel but no tape or rewinding - ignore button
                        print(f"[{ts()}] [VCR] Button ignored (no tape or rewinding)")
                    else:
                        # Normal mode
                        ultimo_estado = estado
                        estado = "evaluando"
                        hubo_giro = False
                        print(f"[{ts()}] [ENCODER] Entrando en modo evaluando (BTN_PRESS)")

            # --- Boton: flanco descendente (solto) ---
            elif line == "BTN_RELEASE":
                if estado == "vcr_hold":
                    # VCR mode: check how long button was held
                    elapsed = time.time() - vcr_btn_press_time

                    # Clear countdown display if it was shown
                    if vcr_countdown_active:
                        trigger_vcr_countdown(None)

                    vcr_btn_press_time = 0.0
                    vcr_countdown_active = False
                    last_countdown_value = None

                    if elapsed >= VCR_REWIND_HOLD_SECONDS:
                        # Already triggered rewind in watchdog
                        print(f"[{ts()}] [VCR] Released after rewind triggered")
                    elif elapsed < VCR_TAP_THRESHOLD and not hubo_giro:
                        # Quick tap (before threshold) without rotation: toggle pause
                        trigger_vcr_pause()
                        print(f"[{ts()}] [VCR] Quick tap ({elapsed:.2f}s) -> pause/play toggle")
                    else:
                        # Released after threshold but before rewind - countdown was cancelled
                        print(f"[{ts()}] [VCR] Hold cancelled after {elapsed:.2f}s")

                    estado = "idle"

                elif estado == "evaluando":
                    if not hubo_giro:
                        if menu_is_open():
                            trigger_menu_select()
                            estado = "idle"
                            print(f"[{ts()}] [ENCODER] Select en menu. Estado=idle")
                        else:
                            trigger_menu()
                            estado = "idle"
                            print(f"[{ts()}] [ENCODER] Evaluando->Menu toggle. Estado=idle")
                    else:
                        estado = ultimo_estado
                        hubo_giro = False
                        print(f"[{ts()}] [ENCODER] Fin de volumen; volvemos a {estado}")

                elif estado == "volume":
                    estado = ultimo_estado
                    hubo_giro = False
                    print(f"[{ts()}] [ENCODER] Fin de ajuste de volumen, volvemos a {estado}")

            elif line == "BTN_NEXT":
                # Saltar al proximo video dentro del canal actual
                trigger_next_video()
                print(f"[{ts()}] [BTN_NEXT] Pulsado")


    except KeyboardInterrupt:
        print(f"\n[{ts()}] [ENCODER] Interrumpido por teclado.")
    finally:
        proc.terminate()
