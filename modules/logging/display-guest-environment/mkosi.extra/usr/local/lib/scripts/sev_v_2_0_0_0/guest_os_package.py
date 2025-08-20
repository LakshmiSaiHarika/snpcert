import sys
import subprocess
import re

class GuestOSPackage:
    """ Maps guest packages/components to their respective OS package names  """

    guest_kernel={}
    guest_kernel["fedora"]="kernel"
    guest_kernel["ubuntu"]="linux-image-virtual"
