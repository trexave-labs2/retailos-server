import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, inspect


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "022_password_reset_tokens.py"
)


def load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_022_password_reset_tokens",
        MIGRATION_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_password_reset_migration_upgrades_and_downgrades_sqlite():
    migration = load_migration()
    engine = create_engine("sqlite:///:memory:")

    with engine.begin() as connection:
        metadata = MetaData()
        Table(
            "users",
            metadata,
            Column("id", Integer, primary_key=True),
        )
        metadata.create_all(connection)

        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        inspector = inspect(connection)
        assert "password_reset_tokens" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("password_reset_tokens")}
        assert {"user_id", "token_hash", "expires_at", "used_at", "created_at"} <= columns
        indexes = {index["name"] for index in inspector.get_indexes("password_reset_tokens")}
        assert "ix_password_reset_user_created" in indexes
        assert "ix_password_reset_expiry" in indexes

        migration.downgrade()
        assert "password_reset_tokens" not in inspect(connection).get_table_names()

    engine.dispose()
