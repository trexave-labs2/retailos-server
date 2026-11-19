import secrets

from server.app.extensions import db
from server.app.models.audit_log import AuditLog
from server.app.models.category import Category
from server.app.models.customer import Customer
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.password_reset_token import PasswordResetToken
from server.app.models.product import Product
from server.app.models.refund import RefundItem
from server.app.models.sale_item import SaleItem
from server.app.models.stock_receiving import StockReceiving
from server.app.models.store import Store
from server.app.models.user import User
from server.app.utils.security import hash_password
from server.app.utils.time import now_utc


class AccountDeletionService:
    """Finalizes expired owner requests without deleting financial/audit history."""

    @classmethod
    def _product_has_history(cls, product_id):
        return any((
            SaleItem.query.filter_by(product_id=product_id).first(),
            RefundItem.query.filter_by(product_id=product_id).first(),
            StockReceiving.query.filter_by(product_id=product_id).first(),
            InventoryMovement.query.filter_by(product_id=product_id).first(),
            cls._has_audit_reference("product", product_id),
        ))

    @staticmethod
    def _has_audit_reference(entity_type, entity_id):
        return AuditLog.query.filter_by(
            entity_type=entity_type,
            entity_id=str(entity_id),
        ).first() is not None

    @classmethod
    def _finalize_store(cls, store, completed_at, audit_action):
        # Customer identity is not needed to preserve a sale or its balance.
        for customer in Customer.query.filter_by(store_id=store.id).all():
            customer.name = f"Deleted customer {customer.id}"
            customer.contact = None

        # Categories have no historical transaction foreign keys.
        products = Product.query.filter_by(store_id=store.id).all()
        for product in products:
            product.category_id = None
            if cls._product_has_history(product.id):
                # Keep referenced catalog rows so sale, refund, receiving, and
                # movement records remain joinable. Remove identifying catalog
                # metadata while preserving the product foreign-key target.
                product.name = f"Archived item {product.id}"
                product.sku = None
                product.barcode = None
                product.cost_price = None
            else:
                db.session.delete(product)

        for category in Category.query.filter_by(store_id=store.id).all():
            if cls._has_audit_reference("category", category.id):
                category.name = f"Archived category {category.id}"
            else:
                db.session.delete(category)

        # The store row must remain: audit_logs.store_id uses ON DELETE RESTRICT.
        # Financial and inventory rows remain attached to this inaccessible store.
        store.name = f"Deleted Store {store.id}"
        store.status = "deleted"
        store.deletion_completed_at = completed_at

        db.session.add(AuditLog(
            store_id=store.id,
            user_id=None,
            action_type=audit_action,
            entity_type="store",
            entity_id=str(store.id),
            details={"completed_at": completed_at.isoformat(), "history_retained": True},
            created_at=completed_at,
        ))

    @classmethod
    def finalize_due_accounts(cls, reference_time=None):
        now = reference_time or now_utc()
        due_users = User.query.filter(
            User.account_status == "pending_deletion",
            User.deletion_scheduled_for.isnot(None),
            User.deletion_scheduled_for <= now,
        ).order_by(User.id.asc()).all()

        finalized_accounts = 0
        finalized_stores = 0
        try:
            for user in due_users:
                stores = Store.query.filter_by(user_id=user.id).all()
                for store in stores:
                    if store.status != "deleted":
                        cls._finalize_store(store, now, "account_deletion_finalized")
                        finalized_stores += 1

                # Remove unused password-reset credentials, not business history.
                PasswordResetToken.query.filter_by(user_id=user.id).delete(synchronize_session=False)
                user.name = "Deleted Account"
                user.email = f"deleted-account-{user.id}@invalid.retailos"
                user.password_hash = hash_password(secrets.token_urlsafe(40))
                user.platform_role = None
                user.account_status = "anonymized"
                user.anonymized_at = now
                user.deletion_requested_at = None
                user.deletion_scheduled_for = None
                user.session_version += 1
                finalized_accounts += 1

            # A store may have its own closure request while the owner account
            # remains active. Process those deadlines independently.
            due_stores = Store.query.filter(
                Store.status == "pending_deletion",
                Store.deletion_scheduled_for.isnot(None),
                Store.deletion_scheduled_for <= now,
            ).order_by(Store.id.asc()).all()
            for store in due_stores:
                cls._finalize_store(store, now, "store_deletion_finalized")
                finalized_stores += 1

            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return {"accounts": finalized_accounts, "stores": finalized_stores}
