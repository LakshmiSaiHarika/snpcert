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

: '
TODO:
- Convert Measurement to the appropriate sha format to pass in as host data
- Build out qemu command line to launch the guest
- Create a service to get guest logging in host
- Check that guest services/test pass correctly
- Figure out host logging
- Add Harikas commits to this branch
'