"""数据源管理 CLI 的参数契约。

这个脚本是 README 教运维敲的唯一入口，此前零测试覆盖。端到端演练能证明它当下可用，
但证明不了它明年还可用——这个项目已经四次发现「没有 CI 覆盖的东西会静默腐烂」。

覆盖重点是**凭据边界**：CLI 刻意不提供任何接收密钥的参数，写进 configuration 会让
数据库备份、审计 payload 和只读数据源接口同时变成密钥泄露面。没有测试挡着，将来
有人为图方便加个 --access-key，评审时未必看得出这是安全回退。
"""

from __future__ import annotations

import pytest

from scripts.sync_data_source import _build_parser, _object_storage_configuration

_SECRET_MARKERS = ("key", "secret", "password", "token", "credential_value")


def _parse(*argv: str):
    return _build_parser().parse_args(argv)


def test_object_storage_configuration_never_carries_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使环境里有密钥，落库的配置也只能有非敏感项。"""

    monkeypatch.setenv("ENTERPRISE_DOCS_ACCESS_KEY", "AKIAREALLOOKINGKEY")
    monkeypatch.setenv("ENTERPRISE_DOCS_SECRET_KEY", "s3cr3t-should-never-be-persisted")
    arguments = _parse(
        "create",
        "--knowledge-base", "kb_default",
        "--name", "手册桶",
        "--type", "object_storage",
        "--endpoint", "s3.example.com",
        "--bucket", "enterprise-docs",
        "--prefix", "handbook/",
        "--region", "cn-north-1",
        "--credential-env", "ENTERPRISE_DOCS",
    )

    configuration = _object_storage_configuration(arguments)

    assert configuration == {
        "endpoint": "s3.example.com",
        "bucket": "enterprise-docs",
        "prefix": "handbook/",
        "region": "cn-north-1",
        "secure": True,
        "credential_env": "ENTERPRISE_DOCS",
    }
    # 键名与值都要查：加了 access_key 字段会被前者抓到，把密钥塞进 name 会被后者抓到。
    serialized = repr(configuration).lower()
    for marker in _SECRET_MARKERS:
        assert marker not in [key.lower() for key in configuration], f"配置不得含 {marker} 字段"
    assert "s3cr3t" not in serialized, "环境变量里的密钥不得出现在配置里"
    assert "akiareallookingkey" not in serialized, "环境变量里的密钥不得出现在配置里"


def test_cli_has_no_parameter_that_accepts_a_secret() -> None:
    """参数表本身就是边界：没有任何选项能把密钥带进来。"""

    options = {
        option
        for action in _build_parser()._actions
        for option in action.option_strings
    }

    for option in options:
        normalized = option.lstrip("-").replace("-", "_").lower()
        assert normalized != "access_key", "不得提供 --access-key"
        assert normalized != "secret_key", "不得提供 --secret-key"
        assert "password" not in normalized, f"不得提供接收口令的参数：{option}"
    assert "--credential-env" in options, "环境变量前缀是读取密钥的唯一途径"


@pytest.mark.parametrize(
    ("missing", "argv"),
    [
        ("endpoint", ("--bucket", "b", "--credential-env", "E")),
        ("bucket", ("--endpoint", "s3.example.com", "--credential-env", "E")),
        ("credential_env", ("--endpoint", "s3.example.com", "--bucket", "b")),
    ],
)
def test_object_storage_requires_endpoint_bucket_and_credential_env(
    missing: str, argv: tuple[str, ...]
) -> None:
    """三项缺一都必须立刻失败。

    尤其是 credential_env：缺了它同步阶段才会以 SOURCE_CREDENTIALS_MISSING 失败，
    而那时数据源已经登记进库，运维要先删再建。
    """

    arguments = _parse("create", "--type", "object_storage", "--name", "桶", *argv)

    with pytest.raises(SystemExit) as error:
        _object_storage_configuration(arguments)

    assert "--credential-env" in str(error.value), "报错要指出正确的参数名"


def test_insecure_flag_maps_to_secure_false() -> None:
    """--insecure 只用于本地 MinIO 一类的 HTTP 端点，默认必须是 HTTPS。"""

    default = _object_storage_configuration(
        _parse(
            "create", "--type", "object_storage", "--name", "桶",
            "--endpoint", "s3.example.com", "--bucket", "b", "--credential-env", "E",
        )
    )
    insecure = _object_storage_configuration(
        _parse(
            "create", "--type", "object_storage", "--name", "桶", "--insecure",
            "--endpoint", "127.0.0.1:9000", "--bucket", "b", "--credential-env", "E",
        )
    )

    assert default["secure"] is True, "默认必须走 HTTPS"
    assert insecure["secure"] is False


def test_default_source_type_stays_local_directory() -> None:
    """不传 --type 时行为不变，V5-6 之前登记数据源的命令继续可用。"""

    assert _parse("create", "--name", "目录", "--root", "/tmp").type == "local_directory"
