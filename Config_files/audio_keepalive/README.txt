# ALSA dmix Configuration for HiFiBerry DAC
# Prevents audio pops/clicks when videos start/stop

## Problem
The HiFiBerry DAC produces audible pops when the audio stream starts or stops.
This happens between video transitions when using direct hardware access (type hw).

## Solution
Configure ALSA with dmix for software audio mixing. The dmix layer maintains
the audio pipeline and prevents the DAC from powering down between streams.

## Installation

# Backup existing ALSA config
sudo cp /etc/asound.conf /etc/asound.conf.backup

# Install new ALSA config with dmix
sudo cp etc/asound.conf /etc/asound.conf

# Restart tvargenta to use new audio config
sudo systemctl restart tvargenta

## Verification
# Test by watching video transitions - there should be no pops between videos
