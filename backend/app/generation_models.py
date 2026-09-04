from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

# 生成模型提供商的统一顺序。
# 这个顺序会影响列表展示和默认激活项的优先级。
PROVIDERS = ("deepseek", "gemini", "kimi")

# 生成模型状态快照，描述一个 provider 当前的配置、状态和可用性。
@dataclass(frozen=True)
class GenerationProviderState:
    provider: str
    model_name: str
    active: bool
    configured: bool
    status: str
    status_code: str | None
    status_message: str
    checked_at: datetime | None
    balance_status: str
    balance_amount: Decimal | None
    balance_currency: str | None
    balance_limit: Decimal | None
    balance_checked_at: datetime | None

# Postgres 模型提供者仓库
class PostgresGenerationProviderRepository:
    """负责维护生成模型各提供商的状态、激活切换和健康检查。"""

    def __init__(self, database_url: str):
        # 保存数据库连接串，后续查询和更新都使用此连接信息。
        self.database_url = database_url

    def synchronize_catalog(
        self,
        catalog: dict[str, tuple[str, bool]],
        default_provider: str,
    ) -> None:
        """同步当前配置中心中的 provider 目录到数据库表。"""
        # 这里以事务方式更新 provider 状态，确保一次同步不会出现部分写入的情况。
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                # 记录同步前已有多少条记录，用来判断是否需要初始化默认激活 provider。
                existing_count = connection.execute(
                    "SELECT count(*) AS count FROM generation_provider_states"
                ).fetchone()["count"]

                # 遍历定义好的 provider 名称，并将配置参数精确写入对应记录。
                for provider in PROVIDERS:
                    model_name, configured = catalog[provider]
                    connection.execute(
                        """INSERT INTO generation_provider_states
                           (provider, model_name, configured, status, status_message)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (provider) DO UPDATE SET
                             model_name=EXCLUDED.model_name,
                             configured=EXCLUDED.configured,
                             status=CASE WHEN NOT EXCLUDED.configured THEN 'unconfigured'
                                         WHEN NOT generation_provider_states.configured THEN 'unavailable'
                                         ELSE generation_provider_states.status END,
                             status_code=CASE WHEN NOT EXCLUDED.configured THEN 'MODEL_KEY_MISSING'
                                              WHEN NOT generation_provider_states.configured THEN NULL
                                              ELSE generation_provider_states.status_code END,
                             status_message=CASE WHEN NOT EXCLUDED.configured THEN 'API Key 未配置'
                                                 WHEN NOT generation_provider_states.configured THEN '等待可用性检测'
                                                 ELSE generation_provider_states.status_message END,
                             updated_at=now()""",
                        (
                            provider,
                            model_name,
                            configured,
                            "unavailable" if configured else "unconfigured",
                            "等待可用性检测" if configured else "API Key 未配置",
                        ),
                    )

                # 如果之前没有任何状态记录，或者当前没有 active provider，
                # 则把 default_provider 设为默认活动提供商。
                active_count = connection.execute(
                    "SELECT count(*) AS count FROM generation_provider_states WHERE active"
                ).fetchone()["count"]
                if existing_count == 0 or active_count == 0:
                    connection.execute(
                        "UPDATE generation_provider_states SET active=(provider=%s), updated_at=now()",
                        (default_provider,),
                    )

    # 获取所有模型提供者状态
    def list(self) -> list[GenerationProviderState]:
        """返回全部 provider 状态，并按优先级顺序展示。"""
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """SELECT provider, model_name, active, configured, status, status_code,
                          status_message, checked_at, balance_status, balance_amount,
                          balance_currency, balance_limit, balance_checked_at
                   FROM generation_provider_states
                   ORDER BY CASE provider WHEN 'deepseek' THEN 1 WHEN 'gemini' THEN 2 ELSE 3 END"""
            ).fetchall()
        return [GenerationProviderState(**row) for row in rows]
    # 获取当前激活的模型提供者
    def active_provider(self) -> str:
        """返回当前激活中的 provider；若不存在则回退为 deepseek。"""
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT provider FROM generation_provider_states WHERE active"
            ).fetchone()
        return str(row["provider"]) if row else "deepseek"

    # 更新模型提供者状态
    def update_status(
        self,
        provider: str,
        *,
        status: str,
        status_code: str | None,
        status_message: str,
        updated_by: str | None = None,
    ) -> GenerationProviderState:
        """更新某个 provider 的健康状态，并返回最新状态对象。"""
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """UPDATE generation_provider_states
                   SET status=%s, status_code=%s, status_message=%s, checked_at=now(),
                       updated_by=COALESCE(%s, updated_by), updated_at=now()
                   WHERE provider=%s
                   RETURNING provider, model_name, active, configured, status, status_code,
                             status_message, checked_at, balance_status, balance_amount,
                             balance_currency, balance_limit, balance_checked_at""",
                (status, status_code, status_message, updated_by, provider),
            ).fetchone()
        if row is None:
            raise ValueError("generation provider not found")
        return GenerationProviderState(**row)

    def update_balance(
        self,
        provider: str,
        *,
        balance_status: str,
        balance_amount: float | None = None,
        balance_currency: str | None = None,
        balance_limit: float | None = None,
    ) -> GenerationProviderState:
        """保存供应商余额快照；余额失败不改变模型可用性状态。"""
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """UPDATE generation_provider_states
                   SET balance_status=%s, balance_amount=%s, balance_currency=%s,
                       balance_limit=%s, balance_checked_at=now(), updated_at=now()
                   WHERE provider=%s
                   RETURNING provider, model_name, active, configured, status, status_code,
                             status_message, checked_at, balance_status, balance_amount,
                             balance_currency, balance_limit, balance_checked_at""",
                (balance_status, balance_amount, balance_currency, balance_limit, provider),
            ).fetchone()
        if row is None:
            raise ValueError("generation provider not found")
        return GenerationProviderState(**row)

    # 激活指定的模型提供者
    def activate(self, provider: str, updated_by: str) -> None:
        """将指定 provider 激活为当前生成模型入口，前提是已配置且可用。"""
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                # 锁定目标 provider，避免并发切换时出现多个 active 同时存在。
                target = connection.execute(
                    """SELECT configured, status FROM generation_provider_states
                       WHERE provider=%s FOR UPDATE""",
                    (provider,),
                ).fetchone()
                if target is None:
                    raise ValueError("generation provider not found")

                # 只有已经配置好并且状态为 available 的 provider 才允许被切换为 active。
                if not target["configured"] or target["status"] != "available":
                    raise ValueError("generation provider is not available")

                # 先清掉当前 active 标记，再设置目标 provider 为 active，保证唯一激活项。
                connection.execute(
                    "UPDATE generation_provider_states SET active=false, updated_at=now() WHERE active"
                )
                connection.execute(
                    """UPDATE generation_provider_states
                       SET active=true, updated_by=%s, updated_at=now() WHERE provider=%s""",
                    (updated_by, provider),
                )
