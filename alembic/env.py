from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.models import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False — иначе alembic глушит логгеры aiogram/app,
    # и ошибки обработчиков бота перестают попадать в логи.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# БД этого проекта общая с как минимум ещё одним другим проектом на том же
# Postgres-инстансе (см. docs/db-table-namespacing.md). Использовать свою,
# суффиксированную таблицу версий, чтобы история миграций этого проекта не
# могла столкнуться с alembic_version другого проекта в той же БД.
VERSION_TABLE = "alembic_version_video_bot"


def _include_object(object, name, type_, reflected, compare_to) -> bool:
    """Скрыть от autogenerate всё, что не относится к этому проекту.

    Без этого фильтра `alembic revision --autogenerate` рано или поздно
    предложит DROP TABLE/DROP COLUMN для объектов другого проекта в той же
    БД просто потому, что их нет в target_metadata этого проекта.
    """
    if type_ == "table":
        return name.endswith("_video_bot")
    if type_ in ("column", "index", "unique_constraint", "foreign_key_constraint"):
        table_name = object.table.name if object.table is not None else name
        return table_name.endswith("_video_bot")
    return True


def _get_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=VERSION_TABLE,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
