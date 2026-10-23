from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from server.app.extensions import db
from server.app.models.store import Store


class Category(db.Model):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(ForeignKey(Store.id, ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    item_type: Mapped[str] = mapped_column(String(20), nullable=False)

    ITEM_TYPES = ("Physical", "Service")

    __table_args__ = (
        UniqueConstraint("store_id", "item_type", "name", name="uq_category_store_type_name"),
        Index("uq_category_store_type_name_ci", "store_id", "item_type", text("lower(name)"), unique=True),
        CheckConstraint("item_type IN ('Physical', 'Service')", name="check_category_item_type"),
    )
