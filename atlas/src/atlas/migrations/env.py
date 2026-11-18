"""How Alembic connects to the database and finds Atlas's tables.

Run from the atlas/ folder:
    uv run alembic revision --autogenerate -m "describe the change"
    uv run alembic upgrade head
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from atlas.config import Settings
from atlas.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Autogenerate compares this (what the models say) with the live database (what exists).
target_metadata = Base.metadata


def database_url() -> str:
    # A URL set programmatically (by `atlas init-db` or the tests) wins over the environment.
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    url = Settings().database_url
    if url is None:
        raise SystemExit("Set DATABASE_URL before running migrations")
    return url


def run_migrations_offline() -> None:
    """Print the SQL instead of running it: `alembic upgrade head --sql`."""
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
