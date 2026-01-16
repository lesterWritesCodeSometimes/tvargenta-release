#!/bin/bash
#
# TVArgenta Installation Script
# This script sets up a Raspberry Pi for TVArgenta
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# =============================================================================
# LEGACY FUNCTION
# Contains any previous installation steps (currently empty)
# =============================================================================
legacy() {
    log_info "Legacy function - no previous installation steps defined"
    # This function is a placeholder for any existing installation logic
    # that was present before the modular refactoring
}

# =============================================================================
# BOOTLOADER SETUP
# Configures /boot/firmware/config.txt and cmdline.txt for TVArgenta
# =============================================================================
setup_bootloader() {
    log_info "Setting up bootloader configuration..."

    local CONFIG_FILE="/boot/firmware/config.txt"
    local CMDLINE_FILE="/boot/firmware/cmdline.txt"

    # Backup original files
    if [[ ! -f "${CONFIG_FILE}.original" ]]; then
        sudo cp "$CONFIG_FILE" "${CONFIG_FILE}.original"
        log_info "Backed up original config.txt"
    fi

    if [[ ! -f "${CMDLINE_FILE}.original" ]]; then
        sudo cp "$CMDLINE_FILE" "${CMDLINE_FILE}.original"
        log_info "Backed up original cmdline.txt"
    fi

    # --- config.txt modifications ---
    log_info "Modifying config.txt..."

    # Comment out onboard audio (we use HiFiBerry DAC)
    if grep -q "^dtparam=audio=on" "$CONFIG_FILE"; then
        sudo sed -i 's/^dtparam=audio=on/#dtparam=audio=on/' "$CONFIG_FILE"
        log_info "Disabled onboard audio"
    fi

    # Switch from vc4-kms-v3d to vc4-fkms-v3d (legacy fake KMS for mpv DRM)
    if grep -q "^dtoverlay=vc4-kms-v3d" "$CONFIG_FILE"; then
        sudo sed -i 's/^dtoverlay=vc4-kms-v3d/#dtoverlay=vc4-kms-v3d\ndtoverlay=vc4-fkms-v3d/' "$CONFIG_FILE"
        log_info "Switched to vc4-fkms-v3d (legacy fake KMS)"
    fi

    # Check if [all] section exists and add our customizations
    if ! grep -q "^dtoverlay=hifiberry-dac" "$CONFIG_FILE"; then
        # Remove any existing [all] section content we might add
        # and append our configuration at the end

        # First, check if there's already an [all] section
        if grep -q "^\[all\]" "$CONFIG_FILE"; then
            # Append after [all] section
            sudo sed -i '/^\[all\]/a\
# TVArgenta HiFiBerry DAC audio setup\
dtparam=i2s=on\
dtoverlay=i2s-mmap\
dtoverlay=hifiberry-dac\
\
# Clean boot appearance\
disable_splash=1\
\
# Disable onboard Bluetooth (using USB dongle)\
dtoverlay=disable-bt' "$CONFIG_FILE"
        else
            # Add [all] section at the end
            echo "" | sudo tee -a "$CONFIG_FILE" > /dev/null
            echo "[all]" | sudo tee -a "$CONFIG_FILE" > /dev/null
            echo "# TVArgenta HiFiBerry DAC audio setup" | sudo tee -a "$CONFIG_FILE" > /dev/null
            echo "dtparam=i2s=on" | sudo tee -a "$CONFIG_FILE" > /dev/null
            echo "dtoverlay=i2s-mmap" | sudo tee -a "$CONFIG_FILE" > /dev/null
            echo "dtoverlay=hifiberry-dac" | sudo tee -a "$CONFIG_FILE" > /dev/null
            echo "" | sudo tee -a "$CONFIG_FILE" > /dev/null
            echo "# Clean boot appearance" | sudo tee -a "$CONFIG_FILE" > /dev/null
            echo "disable_splash=1" | sudo tee -a "$CONFIG_FILE" > /dev/null
            echo "" | sudo tee -a "$CONFIG_FILE" > /dev/null
            echo "# Disable onboard Bluetooth (using USB dongle)" | sudo tee -a "$CONFIG_FILE" > /dev/null
            echo "dtoverlay=disable-bt" | sudo tee -a "$CONFIG_FILE" > /dev/null
        fi
        log_info "Added HiFiBerry DAC and boot customizations"
    else
        log_warn "HiFiBerry DAC config already present, skipping"
    fi

    # Remove enable_uart if present (not needed for production)
    if grep -q "^enable_uart=1" "$CONFIG_FILE"; then
        sudo sed -i 's/^enable_uart=1/#enable_uart=1/' "$CONFIG_FILE"
        log_info "Disabled UART (not needed for production)"
    fi

    # --- cmdline.txt modifications ---
    log_info "Modifying cmdline.txt..."

    local CMDLINE=$(cat "$CMDLINE_FILE")
    local MODIFIED=false

    # Remove serial console if present
    if echo "$CMDLINE" | grep -q "console=serial0,115200"; then
        CMDLINE=$(echo "$CMDLINE" | sed 's/console=serial0,115200 //')
        MODIFIED=true
        log_info "Removed serial console"
    fi

    # Add silent boot parameters if not present
    if ! echo "$CMDLINE" | grep -q "loglevel=0"; then
        CMDLINE="$CMDLINE loglevel=0"
        MODIFIED=true
    fi

    if ! echo "$CMDLINE" | grep -q "systemd.show_status=0"; then
        CMDLINE="$CMDLINE systemd.show_status=0"
        MODIFIED=true
    fi

    if ! echo "$CMDLINE" | grep -q "udev.log_level=3"; then
        CMDLINE="$CMDLINE udev.log_level=3"
        MODIFIED=true
    fi

    if ! echo "$CMDLINE" | grep -q "vt.global_cursor_default=0"; then
        CMDLINE="$CMDLINE vt.global_cursor_default=0"
        MODIFIED=true
    fi

    if ! echo "$CMDLINE" | grep -q "logo.nologo"; then
        CMDLINE="$CMDLINE logo.nologo"
        MODIFIED=true
    fi

    if [[ "$MODIFIED" == true ]]; then
        echo "$CMDLINE" | sudo tee "$CMDLINE_FILE" > /dev/null
        log_info "Added silent boot parameters to cmdline.txt"
    else
        log_warn "cmdline.txt already configured, skipping"
    fi

    log_info "Bootloader setup complete!"
    log_warn "A reboot is required for changes to take effect"
}

# =============================================================================
# INSTALL APP
# Clones the TVArgenta repository to /srv/tvargenta
# =============================================================================
install_app() {
    log_info "Installing TVArgenta application..."

    local INSTALL_DIR="/srv/tvargenta"
    local REPO_URL="https://github.com/lesterWritesCodeSometimes/tvargenta-release.git"
    local CURRENT_USER="${SUDO_USER:-$USER}"

    # Create /srv directory if it doesn't exist
    if [[ ! -d "/srv" ]]; then
        sudo mkdir -p /srv
        log_info "Created /srv directory"
    fi

    # Set ownership of /srv to current user
    sudo chown -R "${CURRENT_USER}:${CURRENT_USER}" /srv
    log_info "Set ownership of /srv to ${CURRENT_USER}"

    # Clone or update the repository
    if [[ -d "${INSTALL_DIR}/.git" ]]; then
        log_info "Repository already exists, pulling latest changes..."
        cd "$INSTALL_DIR"
        sudo -u "$CURRENT_USER" git pull
        log_info "Repository updated"
    else
        if [[ -d "$INSTALL_DIR" ]]; then
            log_warn "Directory exists but is not a git repo, backing up..."
            sudo mv "$INSTALL_DIR" "${INSTALL_DIR}.backup.$(date +%Y%m%d%H%M%S)"
        fi
        log_info "Cloning repository to ${INSTALL_DIR}..."
        sudo -u "$CURRENT_USER" git clone "$REPO_URL" "$INSTALL_DIR"
        log_info "Repository cloned successfully"
    fi

    # Verify the installation
    if [[ -f "${INSTALL_DIR}/app.py" ]]; then
        log_info "TVArgenta application installed successfully"
        log_info "Location: ${INSTALL_DIR}"
    else
        log_error "Installation verification failed - app.py not found"
        exit 1
    fi
}

# =============================================================================
# INSTALL SERVICES
# Installs systemd service files and helper scripts for TVArgenta
# =============================================================================
install_services() {
    log_info "Installing TVArgenta systemd services..."

    local CURRENT_USER="${SUDO_USER:-$USER}"
    local CURRENT_UID=$(id -u "$CURRENT_USER")
    local SERVICE_DIR="/etc/systemd/system"
    local INSTALL_DIR="/srv/tvargenta"
    local CONFIG_DIR="${INSTALL_DIR}/Config_files/servicios_y_scripts_toggle_tva_games"
    local SCRIPTS_DIR="/usr/local/bin"

    # --- Install helper scripts first ---
    log_info "Installing helper scripts to ${SCRIPTS_DIR}..."

    if [[ -d "${CONFIG_DIR}/usr/local/bin" ]]; then
        for script in "${CONFIG_DIR}/usr/local/bin/"*.sh "${CONFIG_DIR}/usr/local/bin/launch-es"; do
            if [[ -f "$script" ]]; then
                local script_name=$(basename "$script")
                sudo cp "$script" "${SCRIPTS_DIR}/${script_name}"
                sudo chmod +x "${SCRIPTS_DIR}/${script_name}"
                log_info "Installed ${script_name}"
            fi
        done
    else
        log_warn "Helper scripts directory not found at ${CONFIG_DIR}/usr/local/bin"
    fi

    # --- Create tvargenta.service (main service - custom content) ---
    log_info "Creating tvargenta.service for user ${CURRENT_USER}..."

    cat << EOF | sudo tee "${SERVICE_DIR}/tvargenta.service" > /dev/null
# /etc/systemd/system/tvargenta.service
[Unit]
Description=TVArgenta Backend (app.py)
Wants=network-online.target
After=network-online.target bluetooth.service
Requires=bluetooth.service

[Service]
User=${CURRENT_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/app.py

# Environment for DBUS and PATH
Environment=DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket
Environment=DBUS_SESSION_BUS_ADDRESS=
Environment="PATH=${INSTALL_DIR}/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"
Environment="FLASK_ENV=production"

Restart=always
RestartSec=2
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tvargenta
KillSignal=SIGINT
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF
    log_info "Created tvargenta.service"

    # --- Create emulationstation-session.service ---
    log_info "Creating emulationstation-session.service..."

    cat << EOF | sudo tee "${SERVICE_DIR}/emulationstation-session.service" > /dev/null
[Unit]
Description=EmulationStation / RetroPie session
After=graphical.target

[Service]
Type=simple
User=${CURRENT_USER}
Group=${CURRENT_USER}
Environment=HOME=/home/${CURRENT_USER}
Environment=USER=${CURRENT_USER}
Environment=LOGNAME=${CURRENT_USER}
Environment=SHELL=/bin/bash
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/${CURRENT_UID}
Environment=TERM=xterm
ExecStart=/opt/retropie/supplementary/emulationstation/emulationstation
Restart=on-abort

[Install]
WantedBy=multi-user.target
EOF
    log_info "Created emulationstation-session.service"

    # --- Create encoder-hotkey.service ---
    log_info "Creating encoder-hotkey.service..."

    cat << EOF | sudo tee "${SERVICE_DIR}/encoder-hotkey.service" > /dev/null
[Unit]
Description=Encoder long-press (2s) / BTN_NEXT -> return to TVArgenta
After=multi-user.target

[Service]
Type=simple
User=root
Group=root
ExecStart=/usr/local/bin/encoder-hotkey-loop.sh
Restart=on-failure
RestartSec=1

[Install]
WantedBy=multi-user.target
EOF
    log_info "Created encoder-hotkey.service"

    # --- Create enter-gaming.service ---
    log_info "Creating enter-gaming.service..."

    cat << EOF | sudo tee "${SERVICE_DIR}/enter-gaming.service" > /dev/null
[Unit]
Description=Switch from TVArgenta to EmulationStation/RetroPie
After=tvargenta.service
Wants=emulationstation-session.service

[Service]
Type=oneshot
User=root
Group=root
ExecStart=/usr/local/bin/enter-gaming-wrapper.sh

[Install]
WantedBy=multi-user.target
EOF
    log_info "Created enter-gaming.service"

    # --- Create return-tvargenta.service ---
    log_info "Creating return-tvargenta.service..."

    cat << EOF | sudo tee "${SERVICE_DIR}/return-tvargenta.service" > /dev/null
[Unit]
Description=Return from EmulationStation to TVArgenta
After=emulationstation-session.service

[Service]
Type=oneshot
User=root
Group=root
ExecStart=/usr/local/bin/return_to_tvargenta.sh

[Install]
WantedBy=multi-user.target
EOF
    log_info "Created return-tvargenta.service"

    # --- Create watch-emustation.service ---
    log_info "Creating watch-emustation.service..."

    cat << EOF | sudo tee "${SERVICE_DIR}/watch-emustation.service" > /dev/null
[Unit]
Description=Watcher: return to TVArgenta when EmulationStation exits
After=emulationstation-session.service
PartOf=enter-gaming.service

[Service]
Type=simple
ExecStart=/usr/local/bin/watch-emustation.sh
Restart=no

[Install]
WantedBy=multi-user.target
EOF
    log_info "Created watch-emustation.service"

    # --- Reload systemd daemon ---
    log_info "Reloading systemd daemon..."
    sudo systemctl daemon-reload

    # --- Enable the main service only ---
    log_info "Enabling tvargenta.service..."
    sudo systemctl enable tvargenta.service

    log_info "Services installed successfully!"
    log_info "Installed services:"
    log_info "  - tvargenta.service (enabled)"
    log_info "  - emulationstation-session.service"
    log_info "  - encoder-hotkey.service"
    log_info "  - enter-gaming.service"
    log_info "  - return-tvargenta.service"
    log_info "  - watch-emustation.service"
    log_warn "Gaming services are installed but not enabled (RetroPie not required)"
    log_warn "The main service will start on next boot, or run: sudo systemctl start tvargenta.service"
}

# =============================================================================
# POWER-ON EXPERIENCE
# Configures the boot splash screen (Plymouth) for a clean black screen
# =============================================================================
poweron_experience() {
    log_info "Setting up power-on experience (boot splash)..."

    local PLYMOUTH_THEME_DIR="/usr/share/plymouth/themes/pix"
    local SPLASH_FILE="${PLYMOUTH_THEME_DIR}/splash.png"
    local SPLASH_WIDTH=1024
    local SPLASH_HEIGHT=768

    # Backup original splash if not already backed up
    if [[ ! -f "${SPLASH_FILE}.original" ]]; then
        sudo cp "$SPLASH_FILE" "${SPLASH_FILE}.original"
        log_info "Backed up original splash.png"
    else
        log_warn "Original splash.png backup already exists"
    fi

    # Create a pure black PNG image using Python PIL
    log_info "Creating black splash image (${SPLASH_WIDTH}x${SPLASH_HEIGHT})..."

    python3 << EOF
from PIL import Image
img = Image.new('RGB', (${SPLASH_WIDTH}, ${SPLASH_HEIGHT}), color='black')
img.save('/tmp/black_splash.png')
print('Black splash image created')
EOF

    # Replace the splash image
    sudo cp /tmp/black_splash.png "$SPLASH_FILE"
    rm /tmp/black_splash.png
    log_info "Replaced splash.png with black image"

    # Update initramfs to include the new splash
    log_info "Updating initramfs (this may take a moment)..."
    sudo update-initramfs -u

    log_info "Power-on experience setup complete!"
    log_warn "A reboot is required for changes to take effect"
}

# =============================================================================
# SETUP PYTHON VIRTUAL ENVIRONMENT
# Creates venv and installs required Python packages
# =============================================================================
setup_venv() {
    log_info "Setting up Python virtual environment..."

    local INSTALL_DIR="/srv/tvargenta"
    local VENV_DIR="${INSTALL_DIR}/venv"
    local CURRENT_USER="${SUDO_USER:-$USER}"

    # Verify app is installed
    if [[ ! -d "$INSTALL_DIR" ]]; then
        log_error "TVArgenta not installed. Run install_app first."
        exit 1
    fi

    # Install system dependencies for Python packages
    log_info "Installing system dependencies..."
    sudo apt-get update
    sudo apt-get install -y \
        python3-venv \
        python3-dev \
        python3-pip \
        libdbus-1-dev \
        libglib2.0-dev \
        pkg-config \
        ffmpeg

    # Create virtual environment if it doesn't exist
    if [[ ! -d "$VENV_DIR" ]]; then
        log_info "Creating virtual environment..."
        sudo -u "$CURRENT_USER" python3 -m venv "$VENV_DIR"
        log_info "Virtual environment created at ${VENV_DIR}"
    else
        log_warn "Virtual environment already exists"
    fi

    # Upgrade pip
    log_info "Upgrading pip..."
    sudo -u "$CURRENT_USER" "$VENV_DIR/bin/pip" install --upgrade pip

    # Install required packages
    log_info "Installing Python packages..."
    sudo -u "$CURRENT_USER" "$VENV_DIR/bin/pip" install \
        Flask \
        Werkzeug \
        Jinja2 \
        websockets \
        Pillow \
        qrcode \
        av \
        gpiozero \
        RPi.GPIO \
        dbus-python \
        python-dotenv \
        psutil \
        python-uinput \
        nfcpy

    log_info "Python virtual environment setup complete!"
}

# =============================================================================
# SETUP AUDIO
# Configures ALSA for HiFiBerry DAC with dmix
# =============================================================================
setup_audio() {
    log_info "Setting up audio configuration..."

    local INSTALL_DIR="/srv/tvargenta"
    local ASOUND_SRC="${INSTALL_DIR}/Config_files/audio_keepalive/etc/asound.conf"
    local ASOUND_DST="/etc/asound.conf"

    # Verify source file exists
    if [[ ! -f "$ASOUND_SRC" ]]; then
        log_error "Source asound.conf not found at ${ASOUND_SRC}"
        log_error "Run install_app first."
        exit 1
    fi

    # Backup existing asound.conf if present
    if [[ -f "$ASOUND_DST" ]] && [[ ! -f "${ASOUND_DST}.original" ]]; then
        sudo cp "$ASOUND_DST" "${ASOUND_DST}.original"
        log_info "Backed up existing asound.conf"
    fi

    # Copy the config file
    sudo cp "$ASOUND_SRC" "$ASOUND_DST"
    log_info "Installed HiFiBerry DAC audio configuration"

    log_info "Audio setup complete!"
    log_warn "A reboot may be required for audio changes to take effect"
}

# =============================================================================
# SETUP DISPLAY
# Configures LightDM for autologin with X11 session
# =============================================================================
setup_display() {
    log_info "Setting up display configuration..."

    local CURRENT_USER="${SUDO_USER:-$USER}"
    local LIGHTDM_CONF="/etc/lightdm/lightdm.conf"
    local ACCOUNTS_DIR="/var/lib/AccountsService/users"
    local ACCOUNTS_FILE="${ACCOUNTS_DIR}/${CURRENT_USER}"
    local X11_SESSION="lightdm-xsession"

    # Backup original lightdm.conf
    if [[ ! -f "${LIGHTDM_CONF}.original" ]]; then
        sudo cp "$LIGHTDM_CONF" "${LIGHTDM_CONF}.original"
        log_info "Backed up original lightdm.conf"
    fi

    # Configure session to use X11 instead of Wayland
    # Replace user-session if it exists
    if grep -q "^user-session=" "$LIGHTDM_CONF"; then
        sudo sed -i "s/^user-session=.*/user-session=${X11_SESSION}/" "$LIGHTDM_CONF"
        log_info "Changed user-session to ${X11_SESSION}"
    fi

    # Replace or add autologin-session
    if grep -q "^autologin-session=" "$LIGHTDM_CONF"; then
        sudo sed -i "s/^autologin-session=.*/autologin-session=${X11_SESSION}/" "$LIGHTDM_CONF"
        log_info "Changed autologin-session to ${X11_SESSION}"
    fi

    # Configure autologin user
    if grep -q "^autologin-user=${CURRENT_USER}$" "$LIGHTDM_CONF" 2>/dev/null; then
        log_warn "Autologin already configured for ${CURRENT_USER}"
    else
        # Add or update [Seat:*] section with autologin
        if grep -q "^\[Seat:\*\]" "$LIGHTDM_CONF"; then
            # Remove any existing autologin-user lines and add new one
            sudo sed -i '/^autologin-user=/d' "$LIGHTDM_CONF"
            sudo sed -i '/^autologin-user-timeout=/d' "$LIGHTDM_CONF"
            sudo sed -i "/^\[Seat:\*\]/a autologin-user=${CURRENT_USER}\nautologin-user-timeout=0" "$LIGHTDM_CONF"
        else
            # Add [Seat:*] section at the end
            echo "" | sudo tee -a "$LIGHTDM_CONF" > /dev/null
            echo "[Seat:*]" | sudo tee -a "$LIGHTDM_CONF" > /dev/null
            echo "autologin-user=${CURRENT_USER}" | sudo tee -a "$LIGHTDM_CONF" > /dev/null
            echo "autologin-user-timeout=0" | sudo tee -a "$LIGHTDM_CONF" > /dev/null
        fi
        log_info "Configured autologin for user ${CURRENT_USER}"
    fi

    # Configure AccountsService to use X11 session (lightdm-xsession)
    sudo mkdir -p "$ACCOUNTS_DIR"

    cat << EOF | sudo tee "$ACCOUNTS_FILE" > /dev/null
[User]
Session=
XSession=${X11_SESSION}
SystemAccount=false
EOF
    log_info "Configured X11 session (${X11_SESSION}) for ${CURRENT_USER}"

    # Ensure user is in required groups for display/input access
    log_info "Adding user to required groups..."
    sudo usermod -aG video,input,gpio "$CURRENT_USER" 2>/dev/null || true

    # Remove gnome-keyring to prevent password prompt on autologin
    # (disabling autostart is not sufficient as it can be started by PAM/session)
    if dpkg -l | grep -q "^ii  gnome-keyring"; then
        log_info "Removing gnome-keyring package..."
        sudo DEBIAN_FRONTEND=noninteractive apt-get remove --purge -y gnome-keyring
        log_info "Removed gnome-keyring (prevents password prompt on autologin)"
    else
        log_info "gnome-keyring not installed, skipping removal"
    fi

    # Disable cloud-init services (not needed, slows boot)
    if systemctl list-unit-files | grep -q "cloud-init"; then
        log_info "Disabling cloud-init services..."
        sudo systemctl disable cloud-init-local.service cloud-init-main.service \
            cloud-init-network.service cloud-config.service cloud-final.service 2>/dev/null || true
        sudo touch /etc/cloud/cloud-init.disabled
        log_info "Disabled cloud-init services"
    fi

    # Disable DPMS and screen blanking (prevent screen from going black)
    log_info "Disabling DPMS and screen blanking..."
    sudo mkdir -p /etc/X11/xorg.conf.d
    cat << 'EOF' | sudo tee /etc/X11/xorg.conf.d/10-disable-dpms.conf > /dev/null
Section "Extensions"
    Option      "DPMS" "Disable"
EndSection

Section "ServerLayout"
    Identifier "ServerLayout0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime"     "0"
    Option "BlankTime"   "0"
EndSection
EOF
    log_info "Created DPMS disable configuration"

    log_info "Display setup complete!"
    log_warn "A reboot is required for display changes to take effect"
}

# =============================================================================
# BUILD ENCODER
# Compiles the encoder_reader C binary for GPIO rotary encoder
# =============================================================================
build_encoder() {
    log_info "Building encoder_reader binary..."

    local INSTALL_DIR="/srv/tvargenta"
    local SOURCE_FILE="${INSTALL_DIR}/encoder_reader.c"
    local OUTPUT_FILE="${INSTALL_DIR}/encoder_reader"
    local CURRENT_USER="${SUDO_USER:-$USER}"

    # Verify source file exists
    if [[ ! -f "$SOURCE_FILE" ]]; then
        log_error "Source file not found at ${SOURCE_FILE}"
        log_error "Run install_app first."
        exit 1
    fi

    # Install libgpiod development package
    log_info "Installing libgpiod development package..."
    sudo apt-get update
    sudo apt-get install -y libgpiod-dev

    # Compile the encoder reader
    log_info "Compiling encoder_reader..."
    sudo -u "$CURRENT_USER" gcc -O2 -o "$OUTPUT_FILE" "$SOURCE_FILE" -lgpiod

    # Verify compilation
    if [[ -x "$OUTPUT_FILE" ]]; then
        log_info "Successfully compiled encoder_reader"
        log_info "Binary location: ${OUTPUT_FILE}"
    else
        log_error "Compilation failed"
        exit 1
    fi

    log_info "Encoder build complete!"
}

# =============================================================================
# MAIN
# =============================================================================
main() {
    echo "========================================"
    echo "  TVArgenta Installation Script"
    echo "========================================"
    echo ""

    # Check if running as root or with sudo available
    if [[ $EUID -ne 0 ]] && ! sudo -v 2>/dev/null; then
        log_error "This script requires sudo privileges"
        exit 1
    fi

    # Parse command line arguments
    case "${1:-}" in
        bootloader)
            setup_bootloader
            ;;
        poweron_experience)
            poweron_experience
            ;;
        install_app)
            install_app
            ;;
        install_services)
            install_services
            ;;
        setup_venv)
            setup_venv
            ;;
        setup_audio)
            setup_audio
            ;;
        setup_display)
            setup_display
            ;;
        build_encoder)
            build_encoder
            ;;
        legacy)
            legacy
            ;;
        all)
            legacy
            setup_bootloader
            poweron_experience
            install_app
            setup_venv
            build_encoder
            install_services
            setup_audio
            setup_display
            ;;
        *)
            echo "Usage: $0 {command}"
            echo ""
            echo "Commands:"
            echo "  bootloader         - Configure boot settings for TVArgenta"
            echo "  poweron_experience - Set up black boot splash screen"
            echo "  install_app        - Clone TVArgenta repo to /srv/tvargenta"
            echo "  setup_venv         - Create Python venv and install packages"
            echo "  build_encoder      - Compile encoder_reader C binary"
            echo "  install_services   - Install systemd services"
            echo "  setup_audio        - Configure ALSA for HiFiBerry DAC"
            echo "  setup_display      - Configure autologin with X11 session"
            echo "  legacy             - Run legacy installation steps"
            echo "  all                - Run all installation steps"
            exit 1
            ;;
    esac
}

main "$@"
