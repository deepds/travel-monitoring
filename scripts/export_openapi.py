"""Выгрузка OpenAPI-спецификации в ``docs/``.

Спецификация — часть поставки: она фиксирует контракт между backend, UI и
внешними потребителями. Запуск: ``python scripts/export_openapi.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DOCS = REPO_ROOT / "docs"


def main() -> int:
    from tco.api.app import create_app

    app = create_app()
    spec = app.openapi()

    DOCS.mkdir(parents=True, exist_ok=True)

    json_path = DOCS / "openapi.json"
    json_path.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    yaml_path = DOCS / "openapi.yaml"
    try:
        import yaml

        yaml_path.write_text(
            yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8",
        )
    except ImportError:  # pragma: no cover - PyYAML входит в зависимости
        yaml_path = None

    paths = spec.get("paths", {})
    operations = sum(
        1
        for methods in paths.values()
        for method in methods
        if method in {"get", "post", "patch", "put", "delete"}
    )

    print(f"OpenAPI {spec['openapi']} — {spec['info']['title']} {spec['info']['version']}")
    print(f"  путей:    {len(paths)}")
    print(f"  операций: {operations}")
    print(f"  схем:     {len(spec.get('components', {}).get('schemas', {}))}")
    print(f"  записано: {json_path.relative_to(REPO_ROOT)}")
    if yaml_path:
        print(f"            {yaml_path.relative_to(REPO_ROOT)}")

    # Контракт обязан быть версионирован с первого релиза (DELTA §5.1).
    unversioned = [path for path in paths if not path.startswith("/api/v1")]
    if unversioned:
        print(f"ОШИБКА: пути вне /api/v1: {unversioned}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
