import sqlalchemy
from sqlalchemy.engine import Engine


def create_database_engine(url: str) -> Engine:
    return sqlalchemy.create_engine(url, pool_pre_ping=True, pool_recycle=1800)
