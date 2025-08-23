import sys
import subprocess
import re

class HostOSPackage:
    """ Maps host package/host components to their respective OS package names  """

    qemu={}
    qemu["fedora"]="qemu"
    qemu["ubuntu"]="qemu-system"
    qemu["debian"]="qemu-system"

    ovmf={}
    ovmf["fedora"]="edk2-ovmf"
    ovmf["ubuntu"]="ovmf"
    ovmf["debian"]="ovmf"

    host_kernel={}
    host_kernel["fedora"]="kernel"
    host_kernel["ubuntu"]="linux-image-virtual"
    host_kernel["debian"]="linux-image-amd64"
