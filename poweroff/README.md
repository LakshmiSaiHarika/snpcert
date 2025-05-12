# Poweroff UKI

This UKI does one simple task: power off. Nothing else.

## Testing

Run the following command. It should boot the image and then immediately power off.

```sh
$ qemu-kvm -m 1G -bios /usr/share/edk2/ovmf/OVMF_CODE.fd -kernel image.efi
```
