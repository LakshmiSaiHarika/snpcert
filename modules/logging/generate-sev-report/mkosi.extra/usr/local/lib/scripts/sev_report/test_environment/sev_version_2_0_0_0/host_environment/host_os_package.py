import sys
import subprocess
import re

class HostOSPackage:
    """ Maps host package/host components to their respective OS package names  """

    qemu={}
    qemu["fedora"]="qemu"
    qemu["ubuntu"]="qemu-system"

    ovmf={}
    ovmf["fedora"]="edk2-ovmf"
    ovmf["ubuntu"]="ovmf"

    host_kernel={}
    host_kernel["fedora"]="kernel"
    host_kernel["ubuntu"]="linux-image-virtual"
