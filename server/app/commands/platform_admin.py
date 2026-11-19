import click
import secrets
from flask import current_app
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from server.app.extensions import db
from server.app.models.platform_audit_log import PlatformAuditLog
from server.app.models.user import PLATFORM_ADMIN_ROLE, SUPER_ADMIN_ROLE, User
from server.app.utils.security import hash_password
from server.app.utils.validators import validate_email, validate_password


@click.group(name="platform-admin")
def platform_admin_cli():
    """Controlled platform-administrator provisioning commands."""


@platform_admin_cli.command("bootstrap")
def bootstrap_platform_admin():
    """Create the configured super administrator during the deployment release step."""
    email = current_app.config.get("PLATFORM_ADMIN_EMAIL")
    password = current_app.config.get("PLATFORM_ADMIN_PASSWORD")
    if validate_email(email):
        raise click.ClickException("PLATFORM_ADMIN_EMAIL must be a valid email address")

    email = email.strip().lower()
    existing_bootstrap = PlatformAuditLog.query.filter_by(action_type="platform_admin.bootstrap").first()
    if existing_bootstrap:
        original = db.session.get(User, int(existing_bootstrap.target_id))
        if not original or original.email.lower() != email:
            raise click.ClickException("The first super administrator has already been provisioned for a different email")
        if original.platform_role == PLATFORM_ADMIN_ROLE:
            original.platform_role = SUPER_ADMIN_ROLE
            db.session.add(PlatformAuditLog(
                actor_user_id=original.id,
                action_type="platform_admin.super_admin_bootstrap_migrated",
                target_type="user",
                target_id=str(original.id),
                details={"migration": "legacy_cli_bootstrap"},
            ))
            db.session.commit()
            click.echo("The existing bootstrap administrator was upgraded to super_admin. Its password was not changed.")
            return
        if original.platform_role != SUPER_ADMIN_ROLE:
            raise click.ClickException("The original bootstrap account no longer has an administrator role")
        click.echo("The configured super administrator is already provisioned; no changes were made.")
        return

    if validate_password(password, minimum_length=12):
        raise click.ClickException("PLATFORM_ADMIN_PASSWORD must contain at least 12 characters and meet the application's password limits")
    if User.query.filter_by(platform_role=SUPER_ADMIN_ROLE).first():
        raise click.ClickException("A super administrator already exists but has no bootstrap audit record; refusing to create another")

    user = User.query.filter(func.lower(User.email) == email).first()
    if user:
        if user.platform_role not in (None, PLATFORM_ADMIN_ROLE):
            raise click.ClickException("The configured account has an incompatible platform role")
        user.platform_role = SUPER_ADMIN_ROLE
        user.admin_password_hash = hash_password(password)
        provisioning_method = "existing_account_promotion"
    else:
        user = User(
            name="Platform Administrator",
            email=email,
            password_hash=hash_password(secrets.token_urlsafe(48)),
            platform_role=SUPER_ADMIN_ROLE,
            admin_password_hash=hash_password(password),
        )
        db.session.add(user)
        db.session.flush()
        provisioning_method = "deployment_environment"

    db.session.add(PlatformAuditLog(
        actor_user_id=user.id,
        action_type="platform_admin.bootstrap",
        target_type="user",
        target_id=str(user.id),
        details={"provisioning_method": provisioning_method},
    ))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if PlatformAuditLog.query.filter_by(action_type="platform_admin.bootstrap").first():
            click.echo("The super administrator was provisioned by another deployment process.")
            return
        raise click.ClickException("Super administrator provisioning failed due to a database conflict")
    click.echo("The main super administrator was provisioned successfully.")
