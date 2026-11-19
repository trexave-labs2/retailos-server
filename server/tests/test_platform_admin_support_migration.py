import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "022_admin_feedback_support.py"
)


class OperationRecorder:
    def __init__(self):
        self.added_columns = []
        self.dropped_columns = []
        self.created_indexes = []
        self.dropped_indexes = []

    def add_column(self, table, column):
        self.added_columns.append((table, column.name, column.nullable, column.server_default.arg))

    def drop_column(self, table, column):
        self.dropped_columns.append((table, column))

    def create_index(self, name, table, columns):
        self.created_indexes.append((name, table, columns))

    def drop_index(self, name, table_name):
        self.dropped_indexes.append((name, table_name))


def load_migration():
    spec = importlib.util.spec_from_file_location("migration_022_admin_feedback_support", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_feedback_support_migration_follows_password_reset_head_and_is_reversible():
    migration = load_migration()
    assert migration.down_revision == "022_password_reset_tokens"

    recorder = OperationRecorder()
    migration.op = recorder
    migration.upgrade()
    migration.downgrade()

    assert recorder.added_columns == [
        ("feedback_submissions", "support_status", False, "new")
    ]
    assert recorder.dropped_columns == [("feedback_submissions", "support_status")]
    assert recorder.created_indexes == [
        (
            "ix_feedback_support_status_created_at",
            "feedback_submissions",
            ["support_status", "created_at"],
        )
    ]
    assert recorder.dropped_indexes == [
        ("ix_feedback_support_status_created_at", "feedback_submissions")
    ]
