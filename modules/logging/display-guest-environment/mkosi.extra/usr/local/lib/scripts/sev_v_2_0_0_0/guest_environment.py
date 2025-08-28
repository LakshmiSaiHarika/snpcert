import sys
import subprocess
import re
from guest_os_package import GuestOSPackage

class GuestEnvironment:
    """ Show guest environment details """

    def get_guest_os_version(self):
        os_version_command = f"hostnamectl | grep \"Operating System\" | cut -d':' -f2"
        # os_version_command = f"cat /etc/os-release | grep PRETTY_NAME"
        command = subprocess.run(os_version_command, shell=True, check=True, text=True, capture_output=True)
        os_version = command.stdout.strip()

        # os_version = os_version.replace("PRETTY_NAME=", "").replace("\"", "").strip()
        guest_os_version = "Guest Operating System: " + os_version
        return guest_os_version

    def get_guest_os_id(self):
        os_version_command = f"grep \'^ID=\' /etc/os-release | cut -d\'=\' -f2"
        os_version_command = os_version_command.strip()
        command = subprocess.run(os_version_command, shell=True, check=True, text=True, capture_output=True)
        guest_os_id = command.stdout.strip()
        return guest_os_id

# Get installed guest package versions
    def get_guest_kernel_version(self):
        os_id = self.get_guest_os_id()
        os_id = os_id.replace('"','')
        kernel_pkg_name = GuestOSPackage.guest_kernel.get(os_id)
        kernel_command = subprocess.run(["/usr/local/lib/scripts/sev_v_2_0_0_0/package_version.sh", kernel_pkg_name], capture_output=True, text=True, check=True)
        guest_kernel_version = kernel_command.stdout.strip()
        return "Guest Kernel Version: " + guest_kernel_version

ge = GuestEnvironment()
print(ge.get_guest_os_version())
print(ge.get_guest_kernel_version())
