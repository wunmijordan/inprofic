"""Registry for recurring INPROFIC maintenance work.

Add future idempotent scheduled commands here so the management command and
the authenticated HTTP trigger always execute the same job set.
"""

from django.core.management import call_command


SCHEDULED_COMMANDS = ("sync_subscriptions", "dispatch_platform_mail")


def run_all_jobs(*, stdout=None):
    completed = []
    for command_name in SCHEDULED_COMMANDS:
        options = {"stdout": stdout} if stdout is not None else {}
        call_command(command_name, **options)
        completed.append(command_name)
    return completed
