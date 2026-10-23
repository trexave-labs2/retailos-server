from datetime import datetime
from server.app.extensions import db
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Numeric, Integer, DateTime, ForeignKey, CheckConstraint, Index, text
from decimal import Decimal
from server.app.models.store import Store
from server.app.utils.time import now_utc


class Product(db.Model):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(ForeignKey(Store.id, ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String)
    product_type: Mapped[str] = mapped_column(String(20), nullable=False, default="Physical")
    base_unit: Mapped[str] = mapped_column(String(20), nullable=False, default="piece")
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    stock_quantity: Mapped[int] = mapped_column(Integer, default=0)
    low_stock_threshold: Mapped[int | None] = mapped_column(Integer)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)

    PRODUCT_TYPES = ("Physical", "Service")

    __table_args__ = (
        CheckConstraint("price >= 0", name="check_price_non_negative"),
        CheckConstraint("cost_price IS NULL OR cost_price >= 0", name="check_cost_price_non_negative"),
        CheckConstraint("stock_quantity >= 0", name="check_stock_quantity_non_negative"),
        CheckConstraint("product_type IN ('Physical', 'Service')", name="check_product_type_valid"),
        Index("uq_products_store_sku_ci", "store_id", text("lower(sku)"), unique=True,
              sqlite_where=text("sku IS NOT NULL"), postgresql_where=text("sku IS NOT NULL")),
        Index("uq_products_store_barcode", "store_id", "barcode", unique=True,
              sqlite_where=text("barcode IS NOT NULL"), postgresql_where=text("barcode IS NOT NULL")),
    )
