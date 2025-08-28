import sys
import subprocess
import re

class HostOSPackage:
    """ Maps host package/host components to their respective OS package names  """

    qemu={}
    qemu["fedora"]="qemu"
    qemu["ubuntu"]="qemu-system"
    qemu["debian"]="qemu-system"
    qemu["centos"]="qemu-kvm-core"

    ovmf={}
    ovmf["fedora"]="edk2-ovmf"
    ovmf["ubuntu"]="ovmf"
    ovmf["debian"]="ovmf"
    ovmf["centos"]="edk2-ovmf"

    host_kernel={}
    host_kernel["fedora"]="kernel"
    host_kernel["ubuntu"]="linux-image-virtual"
    host_kernel["debian"]="linux-image-amd64"
    host_kernel["centos"]="kernel"
