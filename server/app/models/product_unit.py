from decimal import Decimal
from server.app.extensions import db
from server.app.models.product import Product
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, UniqueConstraint


class ProductUnit(db.Model):
    __tablename__ = "product_units"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey(Product.id, ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(20), nullable=False)
    base_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    __table_args__ = (
        UniqueConstraint("product_id", "name", name="uq_product_unit_name"),
        CheckConstraint(
            "base_quantity > 0",
            name="check_product_unit_base_quantity_positive",
        ),
        CheckConstraint(
            "price >= 0",
            name="check_product_unit_price_non_negative",
        ),
    )
