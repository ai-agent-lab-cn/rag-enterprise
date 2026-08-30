"""`.env.example` 与 Settings 的一致性。

照着 `.env.example` 配置的人，理应每一行都真的生效。多出来的键不会报错、不会警告，
只是被静默忽略——`CHROMA_PATH` 在 Chroma 移除后就这样留了下来，照抄的人会以为自己
配置了向量库路径。
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.app.config import Settings


def _documented_keys() -> set[str]:
    text = Path(".env.example").read_text(encoding="utf-8")
    return {
        match.group(1)
        for line in text.splitlines()
        if (match := re.match(r"^([A-Z][A-Z0-9_]*)=", line.strip()))
    }


def test_every_documented_variable_is_a_real_setting() -> None:
    unknown = sorted(_documented_keys() - {name.upper() for name in Settings.model_fields})
    assert unknown == [], f".env.example 里这些键不对应任何配置项，会被静默忽略：{unknown}"


def test_schema_version_in_env_example_matches_the_code() -> None:
    """迁移编号涨了却忘了改这里，照抄的人一启动就会被 schema 检查拒绝。"""

    documented = re.search(
        r"^REQUIRED_DATABASE_SCHEMA_VERSION=(\d+)$",
        Path(".env.example").read_text(encoding="utf-8"),
        re.M,
    )
    assert documented is not None, ".env.example 必须写明所需的 schema 版本"
    assert int(documented.group(1)) == Settings().required_database_schema_version
