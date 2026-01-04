from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float
from sqlalchemy.orm import declarative_base
import datetime

Base = declarative_base()

class Bucket(Base):
    __tablename__ = "buckets"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True)
    bucket_id = Column(Integer, ForeignKey("buckets.id"))

    name = Column(String)
    rarity = Column(Integer)
    crate = Column(String)

    in_min = Column(Float)
    in_max = Column(Float)
    float_value = Column(Float)
    x_value = Column(Float)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True)
    password_hash = Column(String)