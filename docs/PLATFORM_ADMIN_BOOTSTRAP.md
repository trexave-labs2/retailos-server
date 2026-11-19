# Main platform administrator bootstrap

RetailOS provisions one main `super_admin` account from deployment environment
variables. It does not require the account to register first or require manual
database edits.

## Required deployment environment

Set these secrets in the backend hosting provider:

- `PLATFORM_ADMIN_EMAIL`: email address for the main administrator.
- `PLATFORM_ADMIN_PASSWORD`: initial password. Use a unique, strong password
  that meets the application's password policy.

Never put either value in source control, logs, frontend environment variables,
or a committed `.env` file.

## Deployment release command

Run database migrations first, then run the bootstrap command as the hosting
provider's release/pre-deploy command:

```sh
flask --app server.app db upgrade && flask --app server.app platform-admin bootstrap
```

The command is safe to run on every deployment. The first successful run
creates the account (or promotes an already registered account with the
configured email) and records `platform_admin.bootstrap`. Later runs verify
that the same account remains the bootstrap super administrator and make no
password or account changes.

If a legacy deployment already used the old
`PLATFORM_ADMIN_BOOTSTRAP_EMAIL` / `PLATFORM_ADMIN_BOOTSTRAP_TOKEN` CLI
bootstrap, set `PLATFORM_ADMIN_EMAIL` to that same email. The release command
upgrades that original admin to `super_admin` without changing its password.
It refuses to silently create a second root administrator.

Do not run the command before migrations. A failed bootstrap should fail the
release step so deployment is not marked successful without its main admin.

## Roles and permissions

- `super_admin`: the single deployment-provisioned main administrator.
  It can use every platform-admin API and manage administrator privileges.
- `admin`: an administrator appointed by the super administrator. It can use
  platform-admin operational APIs but cannot appoint, revoke, or promote admins.
- no role: an ordinary RetailOS account.

The super administrator can list administrators with
`GET /admin/administrators`. It can grant or revoke the `admin` role with
`PUT /admin/users/{id}/admin-role`.

Grant request:

```json
{"platform_role": "admin"}
```

Revoke request:

```json
{"platform_role": null}
```

The endpoint cannot assign `super_admin`, modify the bootstrap account, or
change the acting super administrator's own role. Repeating the current role
is idempotent and does not create another audit event.

Every actual grant and revocation is committed with an append-only platform
audit event (`platform_admin.role_granted` or
`platform_admin.role_revoked`). Passwords and credentials are never stored
in audit details.

## Operational notes

- The bootstrap password is used only when a new account is created.
- If the configured email already belongs to an account, that account is
  promoted and its existing password is preserved.
- Changing `PLATFORM_ADMIN_PASSWORD` after provisioning does not reset the
  password. Use the normal authenticated password-change flow instead.
- Keep the deployment secrets available to the release command, not just the
  long-running web process.
