"""Командная строка администратора.

Запуск: ``python -m tco.cli <команда>``. Используется в entrypoint контейнера
и в runbook (см. ``docs/RUNBOOK.md``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

from tco.core.config import get_settings
from tco.core.logging import configure_logging, get_logger
from tco.core.utils import utcnow
from tco.db.models import Base
from tco.db.session import get_engine, session_scope
from tco.version import version_payload

logger = get_logger(__name__)


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Заполняет справочники, профили, источники, шаблоны и пользователей."""
    from tco.services.bootstrap import bootstrap_all

    with session_scope() as session:
        report = bootstrap_all(session)

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    generated = report.get("users", {}).get("generated_passwords") or {}
    if generated:
        print(
            "\nВНИМАНИЕ: пароли сгенерированы автоматически и показаны один раз.\n"
            "Сохраните их и задайте постоянные значения через окружение.",
            file=sys.stderr,
        )
    return 0


def cmd_create_tables(args: argparse.Namespace) -> int:
    """Создает таблицы напрямую, минуя Alembic.

    Предназначено только для быстрых локальных проверок: штатный путь
    развертывания — ``alembic upgrade head``.
    """
    if get_settings().environment == "prod" and not args.force:
        print(
            "В prod схема разворачивается миграциями. Используйте 'alembic upgrade head' "
            "или повторите с --force.",
            file=sys.stderr,
        )
        return 2
    Base.metadata.create_all(get_engine())
    print("Таблицы созданы")
    return 0


def cmd_import_scenarios(args: argparse.Namespace) -> int:
    """Импортирует каталог сценариев из CSV или YAML."""
    from tco.services.scenarios import import_scenarios

    path = Path(args.path)
    if not path.exists():
        print(f"Файл не найден: {path}", file=sys.stderr)
        return 2

    fmt = args.format or ("yaml" if path.suffix.lower() in (".yaml", ".yml") else "csv")
    content = path.read_text(encoding="utf-8-sig")

    with session_scope() as session:
        report = import_scenarios(
            session,
            content,
            fmt=fmt,
            created_by="cli",
            source_file=path.name,
            activate=not args.no_activate,
        )
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 1 if report.errors and args.strict else 0


def cmd_run_monitoring(args: argparse.Namespace) -> int:
    """Запускает прогон мониторинга синхронно (без брокера)."""
    from tco.tasks.pipeline import refresh_all_monitoring_scenarios

    result = refresh_all_monitoring_scenarios.apply(
        kwargs={"force_refresh": args.force, "limit": args.limit}
    ).get()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_source_confidence(args: argparse.Namespace) -> int:
    """Пересчитывает Source Confidence по всем источникам."""
    from tco.services.source_metrics import calculate_all_source_confidence

    with session_scope() as session:
        result = calculate_all_source_confidence(session, window_days=args.window_days)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_retention(args: argparse.Namespace) -> int:
    """Применяет политику хранения данных."""
    from tco.services.retention import run_all_retention

    with session_scope() as session:
        result = run_all_retention(session)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    """Проверяет доступность подсистем."""
    from sqlalchemy import text

    from tco.cache.result_cache import get_result_cache
    from tco.storage.raw_store import get_raw_store

    report: dict[str, Any] = {"versions": version_payload(), "checked_at": utcnow().isoformat()}
    ok = True

    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        report["database"] = {"healthy": True}
    except Exception as exc:  # noqa: BLE001
        ok = False
        report["database"] = {"healthy": False, "error": str(exc)}

    try:
        report["raw_storage"] = get_raw_store().health()
        ok = ok and bool(report["raw_storage"].get("available"))
    except Exception as exc:  # noqa: BLE001
        ok = False
        report["raw_storage"] = {"available": False, "error": str(exc)}

    try:
        with session_scope() as session:
            report["result_cache"] = get_result_cache().stats(session)
    except Exception as exc:  # noqa: BLE001
        report["result_cache"] = {"error": str(exc)}

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if ok else 1


def cmd_reset_password(args: argparse.Namespace) -> int:
    """Задает новый пароль пользователю."""
    from tco.core.security import generate_password, hash_password
    from tco.db.models.reference import User

    password = args.password or generate_password()
    with session_scope() as session:
        user = session.scalars(select(User).where(User.username == args.username)).first()
        if user is None:
            print(f"Пользователь {args.username} не найден", file=sys.stderr)
            return 2
        user.password_hash = hash_password(password)
        user.is_active = True

    if not args.password:
        print(f"Новый пароль для {args.username}: {password}")
    else:
        print(f"Пароль пользователя {args.username} обновлен")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tco.cli",
        description="Администрирование платформы мониторинга стоимости путешествий",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap", help="Инициализировать справочники и пользователей").set_defaults(
        func=cmd_bootstrap
    )

    tables = sub.add_parser("create-tables", help="Создать таблицы без Alembic (только dev)")
    tables.add_argument("--force", action="store_true", help="Разрешить и в prod")
    tables.set_defaults(func=cmd_create_tables)

    imp = sub.add_parser("import-scenarios", help="Импортировать каталог сценариев")
    imp.add_argument("path", help="Путь к CSV или YAML")
    imp.add_argument("--format", choices=["csv", "yaml"], help="Формат (по умолчанию по расширению)")
    imp.add_argument("--no-activate", action="store_true", help="Не активировать существующие")
    imp.add_argument("--strict", action="store_true", help="Ненулевой код возврата при ошибках строк")
    imp.set_defaults(func=cmd_import_scenarios)

    mon = sub.add_parser("run-monitoring", help="Прогон мониторинга синхронно")
    mon.add_argument("--force", action="store_true", help="Игнорировать идемпотентность окна")
    mon.add_argument("--limit", type=int, default=None, help="Ограничить число сценариев")
    mon.set_defaults(func=cmd_run_monitoring)

    conf = sub.add_parser("source-confidence", help="Пересчитать Source Confidence")
    conf.add_argument("--window-days", type=int, default=30)
    conf.set_defaults(func=cmd_source_confidence)

    sub.add_parser("retention", help="Применить политику хранения").set_defaults(func=cmd_retention)
    sub.add_parser("health", help="Проверить подсистемы").set_defaults(func=cmd_health)

    pwd = sub.add_parser("reset-password", help="Сбросить пароль пользователя")
    pwd.add_argument("username")
    pwd.add_argument("--password", help="Новый пароль; без него будет сгенерирован")
    pwd.set_defaults(func=cmd_reset_password)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
