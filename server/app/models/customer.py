from datetime import datetime
from decimal import Decimal
from server.app.extensions import db
from server.app.models.store import Store
from server.app.utils.time import now_utc
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import CheckConstraint, String, ForeignKey, DateTime, Integer, Numeric

class Customer(db.Model):
    __tablename__="customers"
    __table_args__ = (
        CheckConstraint(
            "outstanding_balance >= 0",
            name="check_customer_outstanding_balance_non_negative",
        ),
    )
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    store_id:Mapped[int]=mapped_column(ForeignKey(Store.id,ondelete="CASCADE"))
    name:Mapped[str]=mapped_column(String)
    contact:Mapped[str|None]=mapped_column(String)
    outstanding_balance:Mapped[Decimal]=mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime,default=now_utc)
