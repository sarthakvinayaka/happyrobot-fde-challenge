from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Load(Base):
    __tablename__ = "loads"

    load_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    origin: Mapped[str] = mapped_column(String(255), nullable=False)
    destination: Mapped[str] = mapped_column(String(255), nullable=False)
    pickup_datetime: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_datetime: Mapped[str] = mapped_column(String(64), nullable=False)
    equipment_type: Mapped[str] = mapped_column(String(128), nullable=False)
    loadboard_rate: Mapped[float] = mapped_column(Float, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    commodity_type: Mapped[str] = mapped_column(String(255), nullable=False)
    num_of_pieces: Mapped[int] = mapped_column(Integer, nullable=False)
    miles: Mapped[int] = mapped_column(Integer, nullable=False)
    dimensions: Mapped[str] = mapped_column(String(64), nullable=False)
