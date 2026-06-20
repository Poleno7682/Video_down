from __future__ import annotations

from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command


def main() -> None:
    alembic_cfg = AlembicConfig("alembic.ini")
    alembic_command.upgrade(alembic_cfg, "head")
    print("Database initialized successfully.")


if __name__ == "__main__":
    main()
