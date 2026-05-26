"""
MongoDB migration CLI tool.

This module provides a command-line interface for managing MongoDB database migrations.
"""

import sys
import logging
import argparse
from pathlib import Path

from .manager import MigrationManager

# Module-level logger for this file
logger = logging.getLogger(__name__)


def show_help():
    """Display help information"""
    help_text = """
🗃️  MongoDB Migration Tool (based on Beanie)

📋 Commands:
  new-migration -n <name>         Create new migration file
  migrate                         Run all pending migrations
  migrate --distance N            Run N migrations
  migrate --backward             Roll back all migrations
  migrate --backward --distance N Roll back N migrations

🔧 Environment Variables:
  MONGODB_URI              Complete MongoDB connection string
  Or configure separately:
  MONGODB_HOST            MongoDB host (default: localhost)
  MONGODB_PORT            MongoDB port (default: 27017)
  MONGODB_USERNAME        MongoDB username
  MONGODB_PASSWORD        MongoDB password
  MONGODB_DATABASE        MongoDB database (default: memsys)

💡 Examples:
  python -m core.oxm.mongo.migration.cli new-migration -n add_user_index
  python -m core.oxm.mongo.migration.cli migrate
  python -m core.oxm.mongo.migration.cli migrate --distance 1
  python -m core.oxm.mongo.migration.cli migrate --backward --distance 1

⚠️  Notes:
  - Migrations use transactions by default (requires MongoDB replica set)
  - Use --no-use-transaction to disable transactions
  - Migrations are executed in alphabetical order
  - Files starting with underscore are ignored (e.g. __init__.py)
"""
    logger.info(help_text)


def main():
    """Main CLI entry point"""
    if len(sys.argv) == 1 or (
        len(sys.argv) == 2 and sys.argv[1] in ["--help", "-h", "help"]
    ):
        show_help()
        return

    # Parse global arguments
    parser = argparse.ArgumentParser(description="MongoDB Migration Tool")
    parser.add_argument(
        "--uri", help="MongoDB connection URI (overrides environment variables)"
    )
    parser.add_argument(
        "--database", help="MongoDB database name (overrides environment variables)"
    )
    parser.add_argument("--path", type=Path, help="Custom migrations directory path")

    # Parse subcommands
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # new-migration subcommand
    new_migration_parser = subparsers.add_parser(
        "new-migration", help="Create new migration"
    )
    new_migration_parser.add_argument(
        "-n", "--name", required=True, help="Migration name"
    )

    # migrate subcommand
    migrate_parser = subparsers.add_parser("migrate", help="Run migrations")
    migrate_parser.add_argument(
        "--distance", type=int, help="Number of migrations to run"
    )
    migrate_parser.add_argument(
        "--backward", action="store_true", help="Roll back migrations"
    )
    migrate_parser.add_argument(
        "--no-use-transaction", action="store_true", help="Disable transactions"
    )
    migrate_parser.add_argument(
        "--stream-output",
        action="store_true",
        help="Stream child process output to current stdout/stderr in real time",
    )

    # Parse arguments
    args = parser.parse_args()

    try:
        # Create manager instance directly
        manager = MigrationManager(
            uri=args.uri,
            database=args.database,
            migrations_path=args.path or MigrationManager.MIGRATIONS_DIR,
            use_transaction=(
                not args.no_use_transaction if args.command == "migrate" else True
            ),
            distance=args.distance if args.command == "migrate" else None,
            backward=args.backward if args.command == "migrate" else False,
            stream_output=(args.stream_output if args.command == "migrate" else False),
        )

        if args.command == "new-migration":
            try:
                filepath = manager.create_migration(args.name)
                logger.info("🎉 Migration file created successfully!")
                logger.info(f"📝 Please edit file: {filepath}")  # noqa: G004

            except Exception as e:  # noqa: BLE001
                logger.error(f"❌ Failed to create migration: {e}")  # noqa: G004
                sys.exit(1)

        elif args.command == "migrate":
            # Run migration
            exit_code = manager.run_migration()

            if exit_code == 0:
                logger.info("🎉 Migration execution completed!")
            else:
                logger.error("❌ Migration execution failed!")
                sys.exit(exit_code)

        else:
            logger.error(f"❌ Unknown command: {sys.argv[1]}")  # noqa: G004
            show_help()
            sys.exit(1)

    except Exception as e:  # noqa: BLE001
        logger.error(f"❌ Error: {e}")  # noqa: G004
        sys.exit(1)


if __name__ == "__main__":
    main()
