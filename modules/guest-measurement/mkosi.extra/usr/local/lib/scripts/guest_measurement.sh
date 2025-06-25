#!/bin/bash

PKG_OVMF_PATH="/usr/share/edk2/ovmf/OVMF.amdsev.fd"
BUILD_OVMF_PATH="/usr/share/build/ovmf/OVMF.fd"
EFI_PATH="/usr/local/lib/guest-image/guest.efi"
MEASUREMENT_FILE="/usr/local/lib/guest-image/guest_measurement.txt"

# Check which OVMF binary to use
if [ -f "$PKG_OVMF_PATH" ]; then
    OVMF_PATH="$PKG_OVMF_PATH"
    echo "Using packaged OVMF binary: $OVMF_PATH"
elif [ -f "$BUILD_OVMF_PATH" ]; then
    OVMF_PATH="$BUILD_OVMF_PATH"
    echo "Using built OVMF binary: $OVMF_PATH"
else
    echo "Error: Could not find OVMF binary at $PKG_OVMF_PATH or $BUILD_OVMF_PATH" >&2
    exit 1
fi


snpguest generate measurement \
    --vcpu-type EPYC-v4 \
    --ovmf "$OVMF_PATH" \
    --kernel "$EFI_PATH" \
    --output-format hex \
    --measurement-file "$MEASUREMENT_FILE"
