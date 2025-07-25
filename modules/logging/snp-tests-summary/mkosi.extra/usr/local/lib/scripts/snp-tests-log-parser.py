import sys
import json
import subprocess
import importlib
from collections import defaultdict
import emoji as em
import datetime

def fix_message_format(message):
    """Decode if the journal message is a list of character codes."""
    if isinstance(message, list):
        try:
            message = bytes(message).decode("utf-8", "replace")
        except Exception:
            message = ""
    return message

def get_snp_test_journal_entries():
    """Collect and return all the SNP test journal entries with subprocess."""
    proc = subprocess.Popen(
        ["journalctl", "TEST=TRUE", "-o", "json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    snp_test_journal_entries = []

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            snp_test_journal_entries.append(entry)
        except Exception:
            continue

    proc.stdout.close()
    proc.wait()
    return snp_test_journal_entries

def get_snp_host_test_journal_summary():
    """Collect and return all the SNP host test journal entries."""
    print("get_snp_host_test_journal_sumary")
    proc = subprocess.Popen(
        ["journalctl", "SNP_HOST_TEST_LOG=TRUE", "-o", "json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    snp_host_test_entires = []

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            snp_host_test_entires.append(entry)
        except Exception:
            continue

    proc.stdout.close()
    proc.wait()

    test_status_emojis = {
        'done': em.emojize(':check_mark_button:'),
        'failed': em.emojize(':cross_mark:'),
        'skipped': em.emojize(':fast_forward:', language='alias')
    }

    snp_host_test_statuses = gather_snp_test_statuses(snp_host_test_entires)

    snp_host_test_summary = ''
    for snp_test_service, snp_test_status in snp_host_test_statuses.items():
        snp_test_status_emoji = test_status_emojis.get(snp_test_status.lower(), '?')
        snp_host_test_summary += f"{snp_test_status_emoji} {snp_test_service} \n"
        snp_host_test_description=f"systemctl show -p Description {snp_test_service} | cut -d'=' -f2"
        snp_host_test_description=get_service_description(snp_test_service)
        snp_host_test_summary += "     " + snp_host_test_description + "\n"

    print("SNP Host Test Summary \n")
    print(snp_host_test_summary)

def gather_snp_test_statuses(snp_test_journal_entries):
    """Extract all the SNP test service statuses."""
    snp_test_result_entries = [obj for obj in snp_test_journal_entries if "JOB_RESULT" in obj]
    snp_test_statuses = {entry["UNIT"]: entry["JOB_RESULT"] for entry in snp_test_result_entries}
    return snp_test_statuses

def group_messages_by_snp_test_service(snp_test_journal_entries):
    """Group the journal messages by the SNP test service unit."""
    snp_test_service_message = defaultdict(list)
    snp_test_name = defaultdict()

    for obj in snp_test_journal_entries:
        key = obj.get("_SYSTEMD_UNIT")
        message = obj.get("MESSAGE")
        test_name = obj.get("TEST_NAME")
        if key and message:
            snp_test_service_message[key].append(fix_message_format(message))
            snp_test_name[key] = test_name

    snp_test_service_message = {key: "\n".join(values) for key, values in snp_test_service_message.items()}
    return snp_test_service_message, snp_test_name

def get_service_description(snp_test_service):
    """Get the SNP test service description"""
    try:
        snp_test_description = subprocess.check_output(['systemctl', 'cat', snp_test_service], text=True)
        for line in snp_test_description.splitlines():
            if line.startswith('Description='):
                return line.split('=', 1)[1].strip()
        return "No description available"
    except subprocess.CalledProcessError:
        return "Failed to retrieve description"

def generate_snp_test_summary_content(snp_test_statuses, snp_test_name):
    """Display SNP test status summary  with emojis."""
    test_status_emojis = {
        'done': em.emojize(':check_mark_button:'),
        'failed': em.emojize(':cross_mark:'),
        'skipped': em.emojize(':fast_forward:', language='alias')
    }

    snp_test_summary_content = ''
    for snp_test_service, snp_test_status in snp_test_statuses.items():
        snp_test_status_emoji = test_status_emojis.get(snp_test_status.lower(), '?')
        snp_test = snp_test_name.get(snp_test_service, 'Unknown Test')
        snp_test_summary_content += f"{snp_test_status_emoji} {snp_test_service}: {snp_test}\n"
        snp_test_description=f"systemctl show -p Description {snp_test_service} | cut -d'=' -f2"
        snp_test_description=get_service_description(snp_test_service)
        snp_test_summary_content += "     " + snp_test_description + "\n"

    return snp_test_summary_content

def human_readable_timestamp(microseconds):
    # Convert microseconds to seconds and then to a datetime object
    timestamp = int(microseconds) / 1_000_000
    dt = datetime.datetime.utcfromtimestamp(timestamp)
    return dt.strftime('%Y-%m-%d %H:%M:%S.%f UTC')

def generate_snp_complete_log_message(snp_test_journal_entries):
    snp_log_lines = []
    for entry in snp_test_journal_entries:
        raw_timestamp = entry.get('__REALTIME_TIMESTAMP', 0)
        readable_timestamp = human_readable_timestamp(raw_timestamp)

        # Get and fix the log message
        message = fix_message_format(entry.get('MESSAGE', ''))

        # Show the log line
        log_line = f"[{readable_timestamp}] {message}"
        snp_log_lines.append(log_line)

    snp_log_message_content = "\n".join(snp_log_lines)
    return snp_log_message_content

def main():
    snp_test_journal_entries = get_snp_test_journal_entries()
    snp_test_statuses = gather_snp_test_statuses(snp_test_journal_entries)
    snp_test_service_message, snp_test_name = group_messages_by_snp_test_service(snp_test_journal_entries)

    snp_test_summary_title = "\n=== SNP Certification Test Results === \n"
    snp_test_summary_content = generate_snp_test_summary_content(snp_test_statuses, snp_test_name)
    get_snp_host_test_journal_summary()

    snp_test_summary = snp_test_summary_title + "\n" + snp_test_summary_content
    print( snp_test_summary )

    snp_log_message_title = "\n=== View the complete SNP test log === \n"
    snp_log_message_content = generate_snp_complete_log_message(snp_test_journal_entries)

    snp_log_message = snp_log_message_title + "\n" + snp_log_message_content
    print( snp_log_message )

    snp_test_result = snp_test_summary + "\n" + snp_log_message
    pastebin_service_message=f"echo '{snp_test_result}' | aha | html2text | fpaste"

    print("\nSNP Certification results are posted at: \n")
    subprocess.run(pastebin_service_message, shell=True, check=True)


if __name__ == "__main__":
    main()
