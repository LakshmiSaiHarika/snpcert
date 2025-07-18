import sys
import json
import subprocess
import importlib
from collections import defaultdict
import emoji as em

def fix_message(message):
    """Decode if message is a list of character codes."""
    if isinstance(message, list):
        try:
            message = bytes(message).decode("utf-8", "replace")
        except Exception:
            message = ""
    return message

def get_journal_entries():
    """Collect and return journal entries with subprocess."""
    proc = subprocess.Popen(
        ["journalctl", "TEST=TRUE", "-o", "json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    all_entries = []

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            all_entries.append(entry)
        except Exception:
            continue

    proc.stdout.close()
    proc.wait()
    return all_entries

def gather_test_statuses(entries):
    """Extract test service statuses."""
    test_result_entries = [obj for obj in entries if "JOB_RESULT" in obj]
    statuses = {entry["UNIT"]: entry["JOB_RESULT"] for entry in test_result_entries}
    return statuses

def group_messages_by_service(entries):
    """Group messages by service unit."""
    grouped = defaultdict(list)
    testname_dict = defaultdict()

    for obj in entries:
        key = obj.get("_SYSTEMD_UNIT")
        message = obj.get("MESSAGE")
        testname = obj.get("TEST_NAME")
        if key and message:
            grouped[key].append(fix_message(message))
            testname_dict[key] = testname

    grouped = {key: "\n".join(values) for key, values in grouped.items()}
    return grouped, testname_dict

def display_test_content(statuses, testname_dict):
    """Display test results with emojis."""
    status_emojis = {
        'done': em.emojize(':check_mark_button:'),
        'failed': em.emojize(':cross_mark:'),
        'skipped': em.emojize(':fast_forward:', language='alias')
    }

    test_summary_content = ''
    for service_name, status in statuses.items():
        test_status_emoji = status_emojis.get(status.lower(), '?')
        test_name = testname_dict.get(service_name, 'Unknown Test')
        test_summary_content += f"{test_status_emoji} {service_name}: {test_name}\n"

    return test_summary_content

def main():
    all_journal_entries = get_journal_entries()
    all_test_statuses = gather_test_statuses(all_journal_entries)
    grouped_messages, testname_dict = group_messages_by_service(all_journal_entries)

    test_summary_title = "\n=== SNP Certification Test Results === \n"
    test_summary_content = display_test_content(all_test_statuses, testname_dict)

    test_summary = test_summary_title + "\n" + test_summary_content
    print( test_summary )

    log_message_title = "\n=== View the complete SNP test log === \n"
    log_message_content = "\n".join(fix_message(entry.get('MESSAGE', '')) for entry in all_journal_entries)

    log_message = log_message_title + "\n" + log_message_content
    print( log_message )

    snp_test_result = test_summary + "\n" + log_message
    pastebin_service_message=f"echo '{snp_test_result}' | aha | html2text | fpaste"

    print("\n SNP Certification results are posted at: \n")
    subprocess.run(pastebin_service_message, shell=True, check=True)

if __name__ == "__main__":
    main()
