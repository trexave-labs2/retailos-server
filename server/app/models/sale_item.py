from decimal import Decimal
from server.app.extensions import db
from server.app.models.sale import Sale
from server.app.models.product import Product
from server.app.models.product_unit import ProductUnit
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import ForeignKey,Integer,Numeric,CheckConstraint,String

class SaleItem(db.Model):
    __tablename__="sale_items"
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    sale_id:Mapped[int]=mapped_column(ForeignKey(Sale.id,ondelete="CASCADE"))
    product_id:Mapped[int]=mapped_column(ForeignKey(Product.id,ondelete="RESTRICT"))
    unit_id:Mapped[int | None]=mapped_column(ForeignKey(ProductUnit.id,ondelete="RESTRICT"), nullable=True)
    unit_name_at_sale:Mapped[str | None]=mapped_column(String(20), nullable=True)
    unit_base_quantity:Mapped[int]=mapped_column(Integer, nullable=False, default=1)
    base_quantity_deducted:Mapped[int]=mapped_column(Integer, nullable=False, default=1)
    quantity:Mapped[int]=mapped_column(Integer)
    price_at_sale:Mapped[Decimal]=mapped_column(Numeric(10,2))
    total:Mapped[Decimal]=mapped_column(Numeric(12,2))
    __table_args__=(CheckConstraint("quantity > 0",name="check_quantity_positive"),CheckConstraint("unit_base_quantity > 0",name="check_unit_base_quantity_positive"),CheckConstraint("base_quantity_deducted > 0",name="check_base_quantity_deducted_positive"),CheckConstraint("price_at_sale >= 0",name="check_price_at_sale_non_negative"),CheckConstraint("total >= 0",name="check_total_non_negative"))
