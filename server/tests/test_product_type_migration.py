import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "004_product_type.py"
)


class OperationRecorder:
    def __init__(self):
        self.added_columns = []
        self.created_constraints = []
        self.dropped_constraints = []
        self.dropped_columns = []
        self.altered_columns = []

    def add_column(self, table_name, column):
        self.added_columns.append((table_name, column.name, column.server_default.arg))

    def create_check_constraint(self, name, table_name, condition):
        self.created_constraints.append((name, table_name, condition))

    def drop_constraint(self, name, table_name, type_):
        self.dropped_constraints.append((name, table_name, type_))

    def drop_column(self, table_name, column_name):
        self.dropped_columns.append((table_name, column_name))

    def alter_column(self, *args, **kwargs):
        self.altered_columns.append((args, kwargs))


def load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_004_product_type",
        MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_product_type_migration_is_reversible():
    migration = load_migration()
    recorder = OperationRecorder()
    migration.op = recorder

    migration.upgrade()
    migration.downgrade()

    assert recorder.added_columns == [
        ("products", "product_type", "Physical")
    ]
    assert recorder.created_constraints == [
        (
            "check_product_type_valid",
            "products",
            "product_type IN ('Physical', 'Service')",
        )
    ]
    assert recorder.dropped_constraints == [
        ("check_product_type_valid", "products", "check")
    ]
    assert recorder.dropped_columns == [
        ("products", "product_type")
    ]
