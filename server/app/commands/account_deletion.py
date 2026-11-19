import click

from server.app.services.account_deletion_service import AccountDeletionService


@click.group(name="account-deletion")
def account_deletion_cli():
    """Account deletion retention and finalization operations."""


@account_deletion_cli.command("finalize-due")
def finalize_due_account_deletions():
    """Finalize account and store requests whose 30-day recovery period has expired."""
    counts = AccountDeletionService.finalize_due_accounts()
    click.echo(
        f"Finalized {counts['accounts']} account deletion request(s) "
        f"and {counts['stores']} store deletion request(s)."
    )
