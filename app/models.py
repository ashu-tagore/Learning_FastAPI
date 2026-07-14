from sqlalchemy import Column, Integer, String, Boolean
from sqlalchemy.sql.expression import text
from sqlalchemy.sql.sqltypes import TIMESTAMP 
from .database import Base

class Post(Base): # ORM model that maps to the 'posts' table in Postgres
    __tablename__ = "posts"

    id = Column(Integer, primary_key = True, nullable = False)
    title = Column(String, nullable = False)
    content = Column(String, nullable = False)
    published = Column(Boolean, server_default = "True", nullable=False) # Defaults to True at the database level if not provided
    created_at = Column(TIMESTAMP(timezone=True), nullable = False, server_default = text('now()')) # Database sets this to the current time when the row is created