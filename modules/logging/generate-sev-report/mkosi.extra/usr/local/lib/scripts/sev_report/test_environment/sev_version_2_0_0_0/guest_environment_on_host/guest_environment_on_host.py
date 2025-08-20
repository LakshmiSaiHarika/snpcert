import sys
import subprocess
import textwrap

class GuestEnvironment:
    """ Show guest environment details """

    guest_logs_path = "/var/log/journal/guest-logs/"
    guest_environment_metadata = "GUEST_ENVIRONMENT=2.0.0-0"

    def show_guest_environment_on_host(self):

        guest_environment = "\n Guest Environment Details: \n "

        ge_on_host_command = f"journalctl -D {self.guest_logs_path} {self.guest_environment_metadata} -o cat | grep -v .service"
        command = subprocess.run(ge_on_host_command, shell=True, check=True, text=True, capture_output=True)
        guest_environment_result = command.stdout.strip()
        guest_environment_result = textwrap.indent(guest_environment_result, "\t")

        guest_environment += guest_environment_result
        return guest_environment
