"""索引版本的输入文档快照。

这些用例守的是一件事：**一个索引版本的输入集合，在它创建之后就不再变化。**

此前没有快照，`enqueue_rebuild` 列举待建清单与 `finalize_building_version` 计算覆盖率
分母是两条不同时刻执行的查询。两次之间上传一份新资料，分母就多一，那次本已覆盖完整的
构建会被判为 failed；反过来删掉一份，分母变小，一次漏建反而会被判为完整。两种都不报错，
只是悄悄给出错误结论。
"""

from __future__ import annotations

import os

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.app.document_snapshots import (
    create_snapshot,
    get_snapshot,
    list_members,
    snapshot_fingerprint,
)
from backend.app.index_validation import get_report
from backend.app.index_versions import create_building_version, finalize_building_version

from backend.tests.test_index_versions import (
    DATA_SOURCE_ID,
    EMBEDDING_DIMENSION,
    KNOWLEDGE_BASE_ID,
    _add_chunks,
    _add_document,
    _reset,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector"
)


def _database_url() -> str:
    return os.environ["TEST_DATABASE_URL"]


def _build(database_url: str, batch: str = "rbd_snap") -> tuple[str, str]:
    return create_building_version(
        database_url,
        KNOWLEDGE_BASE_ID,
        chunking_version="v1-700-100",
        parser_version="structured-1",
        embedding_model="test/embedding",
        embedding_dimension=EMBEDDING_DIMENSION,
        processing_options={"chunk_size": 700, "chunk_overlap": 100},
        rebuild_batch_id=batch,
    )


def _cover(database_url: str, index_version_id: str, document_version_id: str) -> None:
    """给某个文档版本在该索引版本下写一个分块，模拟索引完成。"""

    _add_chunks(database_url, index_version_id, document_version_id)


def test_fingerprint_ignores_member_order() -> None:
    """指纹按集合算，不按插入顺序——否则同一批输入会得出两个不同的指纹。"""

    assert snapshot_fingerprint([("d1", "v1"), ("d2", "v2")]) == snapshot_fingerprint(
        [("d2", "v2"), ("d1", "v1")]
    )
    assert snapshot_fingerprint([("d1", "v1")]) != snapshot_fingerprint([("d1", "v2")])


def test_snapshot_freezes_the_documents_present_at_creation() -> None:
    database_url = _database_url()
    _reset(database_url)
    _add_document(database_url, "alpha")
    _add_document(database_url, "beta")

    index_version_id, snapshot_id = _build(database_url)

    # 建完版本之后再上传一份资料。
    _add_document(database_url, "gamma")

    members = list_members(database_url, snapshot_id)
    assert [item["document_id"] for item in members] == ["doc_alpha", "doc_beta"]
    assert get_snapshot(database_url, snapshot_id)["included_count"] == 2
    assert index_version_id


def test_snapshot_member_survives_online_document_deletion() -> None:
    """在线资料可删除，但历史 Build 输入事实不能被级联改写。"""

    database_url = _database_url()
    _reset(database_url)
    document_version_id = _add_document(database_url, "alpha")
    _, snapshot_id = _build(database_url)
    before = list_members(database_url, snapshot_id)

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE documents SET current_version_id=NULL WHERE document_id='doc_alpha'"
        )
        connection.execute(
            "DELETE FROM document_versions WHERE document_version_id=%s",
            (document_version_id,),
        )
        connection.execute("DELETE FROM documents WHERE document_id='doc_alpha'")

    after = list_members(database_url, snapshot_id)
    assert after == before
    assert after[0]["filename"] == "alpha.md"
    assert after[0]["content_sha256"]


def test_documents_added_after_the_version_do_not_block_completion() -> None:
    """建版本之后上传的资料不进本次构建，也就不该让本次构建判为不完整。

    这正是快照要挡的回归：用即时查询做分母时，gamma 会让 expected 变成 3，
    而 chunks 只覆盖了 alpha 与 beta，版本会被判 failed——但它其实建完了自己该建的。
    """

    database_url = _database_url()
    _reset(database_url)
    alpha = _add_document(database_url, "alpha")
    beta = _add_document(database_url, "beta")

    index_version_id, _ = _build(database_url)
    _cover(database_url, index_version_id, alpha)
    _cover(database_url, index_version_id, beta)

    _add_document(database_url, "gamma")

    assert finalize_building_version(database_url, index_version_id) == "validating"


def test_chunks_outside_the_snapshot_cannot_fake_coverage() -> None:
    """不属于本次输入的分块不能充抵覆盖率。

    反向的错误同样要挡住：只建了 alpha，却因为 gamma 也在这个版本下有分块而凑够数量。
    覆盖率必须按快照成员逐个核对，不能只比总数。
    """

    database_url = _database_url()
    _reset(database_url)
    alpha = _add_document(database_url, "alpha")
    _add_document(database_url, "beta")

    index_version_id, _ = _build(database_url)
    _cover(database_url, index_version_id, alpha)

    # gamma 不在快照里，但在这个索引版本下有分块。
    gamma = _add_document(database_url, "gamma")
    _cover(database_url, index_version_id, gamma)

    assert finalize_building_version(database_url, index_version_id) == "build_failed"


def test_documents_without_a_current_version_are_recorded_as_excluded() -> None:
    """没有当前版本的文档要留痕，不能静默丢弃——否则分母凭空变小。"""

    database_url = _database_url()
    _reset(database_url)
    _add_document(database_url, "alpha")
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO documents
               (document_id, knowledge_base_id, data_source_id, filename, created_at, updated_at)
               VALUES ('doc_pending', %s, %s, 'pending.md', now(), now())""",
            (KNOWLEDGE_BASE_ID, DATA_SOURCE_ID),
        )

    with psycopg.connect(database_url) as connection, connection.transaction():
        snapshot_id = create_snapshot(
            connection, knowledge_base_id=KNOWLEDGE_BASE_ID, reason="test"
        )

    snapshot = get_snapshot(database_url, snapshot_id)
    assert snapshot["included_count"] == 1
    assert snapshot["excluded_count"] == 1


def test_snapshot_is_bound_to_the_index_version_it_was_created_with() -> None:
    database_url = _database_url()
    _reset(database_url)
    _add_document(database_url, "alpha")

    index_version_id, snapshot_id = _build(database_url)

    with psycopg.connect(database_url) as connection:
        bound = connection.execute(
            "SELECT document_snapshot_id FROM index_versions WHERE index_version_id=%s",
            (index_version_id,),
        ).fetchone()
    assert bound[0] == snapshot_id


def test_a_failed_build_can_be_retried_as_a_new_attempt() -> None:
    """失败的构建重试时新开一次尝试，失败现场保留。

    此前 ensure_index_build 查到同版本的既有行就返回，一个版本永远只有一次 Build——
    「重新构建」这条恢复路径没有落点，失败记录要么被原地覆盖，要么根本重来不了。
    """

    from backend.app.pipeline_governance import ensure_index_build

    database_url = _database_url()
    _reset(database_url)
    _add_document(database_url, "alpha")
    index_version_id, _ = _build(database_url)

    # 用 dict_row 连接调用：生产路径（enqueue_rebuild）就是这么连的，而普通连接下
    # fetchone()[0] 能过、dict_row 下会 KeyError。两条路径都要覆盖，见 CLAUDE.md 第四条。
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        first = ensure_index_build(
            connection, knowledge_base_id=KNOWLEDGE_BASE_ID, index_version_id=index_version_id
        )
        # 进行中的构建是续跑，不是新尝试。
        assert ensure_index_build(
            connection, knowledge_base_id=KNOWLEDGE_BASE_ID, index_version_id=index_version_id
        ) == first

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE index_builds SET status='failed' WHERE index_build_id=%s", (first,)
        )

    with psycopg.connect(database_url) as connection, connection.transaction():
        second = ensure_index_build(
            connection, knowledge_base_id=KNOWLEDGE_BASE_ID, index_version_id=index_version_id
        )

    assert second != first
    with psycopg.connect(database_url) as connection:
        attempts = connection.execute(
            """SELECT attempt_no, status FROM index_builds
               WHERE index_version_id=%s ORDER BY attempt_no""",
            (index_version_id,),
        ).fetchall()
    # 第一次的失败记录仍在。
    assert [(row[0], row[1]) for row in attempts] == [(1, "failed"), (2, "queued")]


def test_cleanup_marks_the_version_cleaned_and_blocks_rollback_to_it() -> None:
    """清理之后版本进入 cleaned，回滚必须拒绝它。

    此前清理只删分块、不改状态，于是「分块还在、能回滚」与「分块已删光、回滚过去
    就是空索引」两种版本在库里完全同形。回滚到后者不会报错——只有用户提问检索不到时
    才会发现知识库空了。
    """

    from backend.app.errors import AppError
    from backend.app.index_versions import cleanup_version, rollback_to_previous

    database_url = _database_url()
    _reset(database_url)
    alpha = _add_document(database_url, "alpha")
    index_version_id, _ = _build(database_url)
    _cover(database_url, index_version_id, alpha)

    with psycopg.connect(database_url) as connection, connection.transaction():
        # active 与 previous 都要求有放行报告（index_versions_active_requires_report）。
        connection.execute(
            """UPDATE index_versions SET status='previous', evaluation_report_id='rep_prev'
               WHERE index_version_id=%s""",
            (index_version_id,),
        )
        connection.execute(
            """INSERT INTO index_versions
               (index_version_id, knowledge_base_id, status, chunking_version, parser_version,
                embedding_model, embedding_dimension, processing_options, config_fingerprint,
                evaluation_report_id, activated_at, created_at)
               VALUES ('iv_current', %s, 'active', 'v1-700-100', 'structured-1',
                       'test/embedding', %s, '{}'::jsonb, %s, 'rep', now(), now())""",
            (KNOWLEDGE_BASE_ID, EMBEDDING_DIMENSION, "c" * 64),
        )
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id='iv_current' WHERE knowledge_base_id=%s",
            (KNOWLEDGE_BASE_ID,),
        )

    # previous 尚未清理时可以回滚。
    rollback_to_previous(database_url, KNOWLEDGE_BASE_ID)

    # 退役后才允许清理：cleanup 只接受 retired / failed。
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE index_versions SET status='retired' WHERE index_version_id=%s",
            (index_version_id,),
        )
    cleanup_version(database_url, index_version_id)

    with psycopg.connect(database_url) as connection:
        status = connection.execute(
            "SELECT status, cleaned_at FROM index_versions WHERE index_version_id=%s",
            (index_version_id,),
        ).fetchone()
    assert status[0] == "cleaned"
    assert status[1] is not None

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE index_versions SET status='previous' WHERE index_version_id=%s",
            (index_version_id,),
        )
    with pytest.raises(AppError) as error:
        rollback_to_previous(database_url, KNOWLEDGE_BASE_ID)
    assert error.value.code == "INDEX_VERSION_ALREADY_CLEANED"


def test_integrity_gate_catches_a_version_that_missed_a_document() -> None:
    """漏建的版本必须被完整性门禁拦下。

    这一层此前完全不存在：唯一的门禁是检索质量，而它比的是评测集上的指标——
    知识库里漏索引一半文档，评测集照样可能全过。
    """

    from backend.app.index_validation import validate_index_version
    from backend.tests.test_index_versions import _fingerprint_of, _passing_report

    database_url = _database_url()
    _reset(database_url)
    alpha = _add_document(database_url, "alpha")
    _add_document(database_url, "beta")
    index_version_id, _ = _build(database_url)
    # 只建了 alpha，beta 漏了。
    _cover(database_url, index_version_id, alpha)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE index_versions SET status='validating' WHERE index_version_id=%s",
            (index_version_id,),
        )

    result = validate_index_version(
        database_url,
        index_version_id,
        _passing_report(_fingerprint_of(database_url, index_version_id)),
    )

    assert result["status"] == "failed"
    assert [item["check_key"] for item in result["failure_items"]] == ["missing_document"]
    assert result["integrity"]["status"] == "fail"
    # 检索质量层照样通过——正是它单独无法发现漏建。
    assert result["retrieval_quality"]["status"] == "pass"


def test_technical_gate_catches_a_dimension_mismatch() -> None:
    """向量维度与版本声明不符必须被技术门禁拦下。"""

    from backend.app.index_validation import check_technical

    database_url = _database_url()
    _reset(database_url)
    alpha = _add_document(database_url, "alpha")
    index_version_id, _ = _build(database_url)
    _cover(database_url, index_version_id, alpha)

    with psycopg.connect(database_url) as connection:
        passing = check_technical(connection, index_version_id)
    assert passing["status"] == "pass"

    # 把版本声明的维度改成与实际分块不符。
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE index_versions SET embedding_dimension=1536 WHERE index_version_id=%s",
            (index_version_id,),
        )
    with psycopg.connect(database_url) as connection:
        failing = check_technical(connection, index_version_id)

    assert failing["status"] == "fail"
    assert [item["check_key"] for item in failing["checks"] if item["status"] == "fail"] == [
        "embedding_dimension"
    ]


def test_integrity_gate_catches_a_gap_in_chunk_indexes() -> None:
    """总数看似正常也不够；每份资料的 chunk_index 必须从 0 连续递增。"""

    from backend.app.index_validation import check_integrity

    database_url = _database_url()
    _reset(database_url)
    alpha = _add_document(database_url, "alpha")
    index_version_id, _ = _build(database_url)
    _add_chunks(database_url, index_version_id, alpha, count=3)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "DELETE FROM chunks WHERE index_version_id=%s AND chunk_index=1",
            (index_version_id,),
        )

    with psycopg.connect(database_url) as connection:
        result = check_integrity(connection, index_version_id)

    assert result["status"] == "fail"
    assert "chunk_index_continuity" in [
        item["check_key"] for item in result["checks"] if item["status"] == "fail"
    ]


def test_complete_component_manifest_requires_the_version_hnsw_index() -> None:
    """新创建的 complete 版本必须用实际物理索引证明 Vector 组件可用。"""

    from backend.app.index_validation import check_technical
    from backend.app.index_versions import component_manifest

    database_url = _database_url()
    _reset(database_url)
    alpha = _add_document(database_url, "alpha")
    manifest = component_manifest(reranker_model="test/reranker")
    index_version_id, _ = create_building_version(
        database_url,
        KNOWLEDGE_BASE_ID,
        chunking_version="v1-700-100",
        parser_version="structured-1",
        embedding_model="test/embedding",
        embedding_dimension=EMBEDDING_DIMENSION,
        processing_options={"chunk_size": 700, "chunk_overlap": 100},
        rebuild_batch_id="rbd_manifest",
        creation_reason="component_upgraded",
        config_snapshot={"components": manifest},
        components=manifest,
    )
    _cover(database_url, index_version_id, alpha)

    with psycopg.connect(database_url) as connection:
        result = check_technical(connection, index_version_id)

    assert result["status"] == "fail"
    assert "vector_index_health" in [
        item["check_key"] for item in result["checks"] if item["status"] == "fail"
    ]


def test_activation_requires_a_persisted_passing_report() -> None:
    """直接把状态改成 ready 也激活不了：激活会核验持久化的验证报告。

    此前门禁依据是调用方传进来的内存对象，库里查不到任何一次验证发生过。
    """

    from backend.app.errors import AppError
    from backend.app.index_versions import switch_to_version

    database_url = _database_url()
    _reset(database_url)
    alpha = _add_document(database_url, "alpha")
    index_version_id, _ = _build(database_url)
    _cover(database_url, index_version_id, alpha)

    # 绕过验证，直接改状态。
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE index_versions SET status='ready' WHERE index_version_id=%s",
            (index_version_id,),
        )

    with pytest.raises(AppError) as error:
        switch_to_version(database_url, index_version_id)
    assert error.value.code == "VALIDATION_NOT_PASSED"


def test_legacy_backfill_restores_activatability_without_faking_a_pass() -> None:
    """升级前上线的版本回填后能重新激活，但报告要如实标注来源。

    V31 把门禁搬进状态机后，没有验证报告的版本一律激活不了。升级前的版本一份报告都没有——
    不回填的话，正在线上跑的版本一旦回滚就再也切不回去。

    但回填的是事实不是结论：三层结果留 unknown，report_source 标 legacy_backfill。
    凭空补一份「全部通过」才是真的伪造质量门禁。
    """

    from scripts.backfill_index_governance import backfill_one, find_candidates

    database_url = _database_url()
    _reset(database_url)
    alpha = _add_document(database_url, "alpha")
    index_version_id, _ = _build(database_url)
    _cover(database_url, index_version_id, alpha)

    # 造一个「升级前上线」的版本：有 active 状态，但没有验证报告。
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE index_versions SET status='active', evaluation_report_id='rep_legacy',
                      activated_at=now(), validation_report_id=NULL
               WHERE index_version_id=%s""",
            (index_version_id,),
        )

    with psycopg.connect(database_url) as connection:
        candidates = find_candidates(connection)
    assert [item["index_version_id"] for item in candidates] == [index_version_id]

    with psycopg.connect(database_url) as connection, connection.transaction():
        report_id = backfill_one(connection, candidates[0])

    stored = get_report(database_url, report_id)
    assert stored["status"] == "pass"
    assert stored["report_source"] == "legacy_backfill"
    # 三层结果必须是 unknown，不能伪装成通过。
    assert stored["integrity_result"]["status"] == "unknown"
    assert stored["technical_result"]["status"] == "unknown"
    assert stored["retrieval_result"]["status"] == "unknown"

    # 幂等：再跑一次不会重复回填。
    with psycopg.connect(database_url) as connection:
        assert find_candidates(connection) == []


def test_bootstrap_version_is_activatable_but_marked_as_ungated() -> None:
    """首次索引引导出来的版本可以回滚后再切回，但必须标明它没经过门禁。

    这条路径有意绕过发布门禁——首版没有前序基线，也没有正在服务的索引可退回，要求它先跑
    三层门禁等于让用户上传第一份文档后无法检索。但绕过门禁不等于可以查不到：报告必须
    存在（否则回滚到首版就再也切不回来），且三层结果必须是 unknown 而不是 pass。
    """

    from backend.app.index_versions import active_or_bootstrap_version, switch_to_version

    database_url = _database_url()
    _reset(database_url)
    _add_document(database_url, "alpha")

    index_version_id = active_or_bootstrap_version(
        database_url,
        KNOWLEDGE_BASE_ID,
        chunking_version="v1-700-100",
        parser_version="structured-1",
        embedding_model="test/embedding",
        embedding_dimension=EMBEDDING_DIMENSION,
        processing_options={"chunk_size": 700, "chunk_overlap": 100},
    )

    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            "SELECT status, validation_report_id FROM index_versions WHERE index_version_id=%s",
            (index_version_id,),
        ).fetchone()
    assert row[0] == "active"
    assert row[1] is not None, "引导版本必须留下报告，否则回滚后切不回来"

    stored = get_report(database_url, row[1])
    assert stored["report_source"] == "bootstrap"
    # 没跑过门禁就不能声称通过。
    assert stored["integrity_result"]["status"] == "unknown"
    assert stored["technical_result"]["status"] == "unknown"
    assert stored["retrieval_result"]["status"] == "unknown"

    # 报告存在，因此把它降级再激活是可行的——这正是回滚后要切回来的场景。
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE index_versions SET status='ready' WHERE index_version_id=%s",
            (index_version_id,),
        )
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id=NULL WHERE knowledge_base_id=%s",
            (KNOWLEDGE_BASE_ID,),
        )
    switch_to_version(database_url, index_version_id)


def test_bootstrap_does_not_duplicate_the_report_on_repeat_calls() -> None:
    """已有 active 版本时不再引导，也不重复写报告。"""

    from backend.app.index_versions import active_or_bootstrap_version

    database_url = _database_url()
    _reset(database_url)
    kwargs = dict(
        chunking_version="v1-700-100",
        parser_version="structured-1",
        embedding_model="test/embedding",
        embedding_dimension=EMBEDDING_DIMENSION,
        processing_options={"chunk_size": 700, "chunk_overlap": 100},
    )
    first = active_or_bootstrap_version(database_url, KNOWLEDGE_BASE_ID, **kwargs)
    second = active_or_bootstrap_version(database_url, KNOWLEDGE_BASE_ID, **kwargs)

    assert first == second
    with psycopg.connect(database_url) as connection:
        total = connection.execute(
            "SELECT count(*) FROM validation_reports WHERE index_version_id=%s", (first,)
        ).fetchone()[0]
    assert total == 1


def test_an_activated_version_may_accumulate_chunks_outside_its_snapshot() -> None:
    """已激活的版本会持续接收增量分块，这不是缺陷——但它意味着完整性门禁只对候选版本成立。

    同步把新资料的分块写进 **active** 版本（`active_or_bootstrap_version`），而该版本的
    文档快照是构建时冻结的。于是激活之后，版本的分块集合必然超出它的快照，
    `orphan_chunk` 会报非零。

    这两件事各自都对：快照描述的是「这次构建的输入」，不是「这个版本此后永远只能有这些」。
    两者不冲突的唯一原因是 `validate_index_version` 拒绝验证 active 版本——门禁只作用于
    尚未激活的候选，而候选不会收到增量写入。

    **这条守卫就是那个前提。** 谁要放宽 `validate_index_version` 的状态限制，
    会先在这里看到为什么不能。
    """

    from backend.app.errors import AppError
    from backend.app.index_validation import check_integrity, validate_index_version

    database_url = _database_url()
    _reset(database_url)
    alpha = _add_document(database_url, "alpha")
    index_version_id, _ = _build(database_url)
    _cover(database_url, index_version_id, alpha)

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE index_versions SET status='active', evaluation_report_id='rep'
               WHERE index_version_id=%s""",
            (index_version_id,),
        )
    # 激活之后新增一份资料，其分块落进这个版本——正是同步的行为。
    beta = _add_document(database_url, "beta")
    _cover(database_url, index_version_id, beta)

    with psycopg.connect(database_url) as connection:
        integrity = check_integrity(connection, index_version_id)
    orphans = [
        item for item in integrity["checks"]
        if item["check_key"] == "orphan_chunk" and item["status"] == "fail"
    ]
    assert orphans, "增量分块本应超出快照——若这里为空，说明同步不再写 active 版本了"

    # 而门禁根本不会跑到这个版本上：状态守卫先一步拒绝。
    with pytest.raises(AppError) as error:
        validate_index_version(database_url, index_version_id, None)
    assert error.value.code == "INDEX_VERSION_NOT_VALIDATABLE"


def test_config_drift_is_reported_with_what_actually_changed() -> None:
    """改完配置后，当前生效的索引是旧配置建的——这件事必须查得到。

    `config_fingerprint` 此前只用于「创建版本时算一次」和「验证时比对报告与版本」，
    没有任何代码拿 active 版本的指纹与当前配置比对。于是改完 chunk_size 之后线上索引
    仍是旧的，可以无限期这样跑：不报错、不提示、页面上看不出来。

    报告必须逐项列出差异——操作者要据此判断值不值得重建（全量重解析加重嵌入），
    「有问题」三个字给不了这个判断。
    """

    from backend.app.index_versions import active_config_drift

    database_url = _database_url()
    _reset(database_url)
    alpha = _add_document(database_url, "alpha")
    index_version_id, _ = _build(database_url)
    _cover(database_url, index_version_id, alpha)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE index_versions SET status='active', evaluation_report_id='rep'
               WHERE index_version_id=%s""",
            (index_version_id,),
        )
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id=%s WHERE knowledge_base_id=%s",
            (index_version_id, KNOWLEDGE_BASE_ID),
        )
        connection.execute(
            """INSERT INTO index_settings (singleton, embedding_model, embedding_dimension)
               VALUES (true, 'test/embedding', %s)
               ON CONFLICT (singleton) DO UPDATE SET embedding_model=EXCLUDED.embedding_model,
                                                     embedding_dimension=EXCLUDED.embedding_dimension""",
            (EMBEDDING_DIMENSION,),
        )

    # 版本建于 chunk_size=700 / overlap=100（见 _build）。配置未变时不该报漂移。
    assert active_config_drift(
        database_url, KNOWLEDGE_BASE_ID, chunk_size=700, chunk_overlap=100
    ) is None

    drift = active_config_drift(
        database_url, KNOWLEDGE_BASE_ID, chunk_size=500, chunk_overlap=80
    )
    assert drift is not None, "改了切分配置却报告无漂移"
    fields = {item["field"] for item in drift["changes"]}
    assert "chunking_version" in fields
    assert "processing_options" in fields
    # 逐项差异要能看出改前改后，不能只说「变了」。
    chunking = next(i for i in drift["changes"] if i["field"] == "chunking_version")
    assert chunking["active"] != chunking["current"]
    assert drift["active_fingerprint"] != drift["current_fingerprint"]


def test_no_active_version_means_no_drift_claim() -> None:
    """没有 active 版本时不能声称漂移——那是无从判断，不是「配置不一致」。"""

    from backend.app.index_versions import active_config_drift

    database_url = _database_url()
    _reset(database_url)
    assert active_config_drift(
        database_url, KNOWLEDGE_BASE_ID, chunk_size=500, chunk_overlap=80
    ) is None


def test_lifecycle_events_record_who_did_what() -> None:
    """激活与回滚要留下带执行者的事件，而不只是改一个状态字段。

    版本表只保留「现在是什么状态」，三个时间戳各自只记最后一次。此前唯一的留痕是通用
    audit 表的两条记录，而它 **actor_id 恒为硬编码的 None**——出事时只能知道
    「有人激活了它」，不知道是谁，也看不到前后状态。
    """

    from backend.app.index_versions import (
        Actor,
        list_lifecycle_events,
        rollback_to_previous,
        switch_to_version,
    )

    database_url = _database_url()
    _reset(database_url)
    alpha = _add_document(database_url, "alpha")
    first, _ = _build(database_url, batch="rbd_one")
    _cover(database_url, first, alpha)

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO validation_reports
               (validation_report_id, index_version_id, status, policy_version,
                evaluation_set_version)
               VALUES ('vr_first', %s, 'pass', 'v1', 'rep_first')""",
            (first,),
        )
        connection.execute(
            """UPDATE index_versions SET status='ready', validation_report_id='vr_first'
               WHERE index_version_id=%s""",
            (first,),
        )

    operator = Actor("usr_0123456789abcdef", "admin")
    switch_to_version(database_url, first, None, operator)

    events = list_lifecycle_events(database_url, first)
    assert [item["event_type"] for item in events] == ["activated", "created"]
    assert list_lifecycle_events(database_url, first, KNOWLEDGE_BASE_ID) == events
    assert list_lifecycle_events(database_url, first, "kb_other") == []
    assert events[0]["actor_id"] == "usr_0123456789abcdef"
    assert events[0]["actor_role"] == "admin"
    assert events[0]["from_status"] == "ready"
    assert events[0]["to_status"] == "active"
    assert events[0]["validation_report_id"] == "vr_first"

    # 再激活一个版本，原 active 应留下一条 deactivated，且能看出被谁取代。
    beta = _add_document(database_url, "beta")
    second, _ = _build(database_url, batch="rbd_two")
    _cover(database_url, second, alpha)
    _cover(database_url, second, beta)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO validation_reports
               (validation_report_id, index_version_id, status, policy_version,
                evaluation_set_version)
               VALUES ('vr_second', %s, 'pass', 'v1', 'rep_second')""",
            (second,),
        )
        connection.execute(
            """UPDATE index_versions SET status='ready', validation_report_id='vr_second'
               WHERE index_version_id=%s""",
            (second,),
        )
    switch_to_version(database_url, second, None, operator)

    assert [item["event_type"] for item in list_lifecycle_events(database_url, first)] == [
        "deactivated", "activated", "created",
    ]

    # 回滚是追加一条方向相反的事件，不是撤销记录。
    rollback_to_previous(database_url, KNOWLEDGE_BASE_ID, None, operator)
    types = [item["event_type"] for item in list_lifecycle_events(database_url, first)]
    assert types == ["rolled_back", "deactivated", "activated", "created"], types


def test_every_declared_lifecycle_event_type_has_a_producer() -> None:
    """迁移里声明的每一种事件类型都必须有代码会写入。

    这是本仓反复出现的一类缺陷：枚举声明得很完整，实际只接了其中几个，剩下的既不会出现
    也不会报错——阶段 0 清理过 `index_definitions`，阶段 1 拒绝加没有产生者的 `draft`
    状态，第 18 步发现前端五个格子后端从不写。**而 index_lifecycle_events 刚落地时
    我自己也犯了同样的错：声明 11 种，只接了 4 种。**

    这条守卫让下一次「先声明后接线」在提交前就红。
    """

    import re
    from pathlib import Path

    sql = Path("backend/migrations/0035_index_lifecycle_events.sql").read_text(encoding="utf-8")
    block = re.search(r"event_type text NOT NULL CHECK \(event_type IN \(([^)]*)\)", sql, re.S)
    assert block, "迁移里找不到 event_type 的 CHECK"
    declared = {value.strip().strip("'") for value in block.group(1).split(",") if value.strip()}

    produced: set[str] = set()
    for path in Path("backend/app").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        # 直接赋值，以及三元表达式两侧：
        # event_type="build_succeeded" if complete else "build_failed"
        for match in re.finditer(r'event_type=("(?:[a-z_]+)"(?:[^,\n]*)?)', text):
            produced |= set(re.findall(r'"([a-z_]+)"', match.group(1)))

    orphans = sorted(declared - produced)
    assert not orphans, (
        f"这些事件类型声明了却没有任何代码会写入：{orphans}。"
        "要么接上产生者，要么从 CHECK 里删掉——留着它只会让人以为系统会记录这些事。"
    )


def test_no_governance_enum_value_is_declared_without_a_producer() -> None:
    """治理表 CHECK 里声明的每个取值，都必须有代码可能写入它。

    这类死值不会出错，只会让人以为系统支持这些状态：读 schema 的人会据此设计前端、
    写监控告警、给运维写手册，而它们一次都不会出现。本轮因此栽过四次——前端为同步流水线
    画了五个后端从不写的格子、`operations` 的两个 operation_type 从未被创建、
    `index_definitions` 整张表是空壳、`index_lifecycle_events` 声明 11 种事件只接了 4 种。

    判据是「有没有产生者」，不是「将来会不会用上」。要加新取值，就同时加写它的代码。
    """

    import os
    import re
    from pathlib import Path

    import psycopg

    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("需要 PostgreSQL")

    from backend.app.database import apply_migrations

    apply_migrations(os.environ["TEST_DATABASE_URL"])
    literals: set[str] = set()
    for directory in ("backend/app", "scripts"):
        for path in Path(directory).glob("*.py"):
            literals |= set(
                re.findall(r"['\"]([a-z][a-z0-9_]{2,30})['\"]", path.read_text(encoding="utf-8"))
            )

    # 这两条与索引治理／数据同步无关，属于评测子系统，不在本条守卫的范围内。
    OUT_OF_SCOPE = {("evaluation_runs", "evaluation_type")}

    governance = (
        "index_versions", "index_builds", "index_lifecycle_events", "validation_reports",
        "document_snapshots", "document_snapshot_members", "document_processing_runs",
        "operations", "sync_runs", "sync_resource_runs",
    )
    orphans: list[str] = []
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        rows = connection.execute(
            """SELECT rel.relname, pg_get_constraintdef(con.oid)
               FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid
               WHERE con.contype='c' AND pg_get_constraintdef(con.oid) LIKE '%ANY (ARRAY%'"""
        ).fetchall()
    for table, definition in rows:
        if table not in governance:
            continue
        column = re.search(r"\((\w+)\)::text = ANY", definition) or re.search(
            r"^CHECK \(\((\w+) = ANY", definition
        )
        if not column or (table, column.group(1)) in OUT_OF_SCOPE:
            continue
        for value in sorted(set(re.findall(r"'([a-z_]+)'::text", definition))):
            if value not in literals:
                orphans.append(f"{table}.{column.group(1)} = {value!r}")

    assert not orphans, (
        "这些取值在 CHECK 里声明了，但没有任何代码会写入：\n  "
        + "\n  ".join(orphans)
        + "\n要么接上产生者，要么从 CHECK 里删掉。"
    )


def test_index_version_response_literal_matches_the_database_domain() -> None:
    """响应模型的状态取值必须与数据库 CHECK 一致。

    V31 扩了 index_versions 的状态域（加 validating / cleaned，把 failed 拆成
    build_failed / validation_failed），同时改了前端类型，**唯独漏了后端响应模型**。
    结果是第一个 build_failed 版本一出现，整个索引版本列表接口直接 500——
    而这在本地一直没暴露，因为测试环境很少留下失败版本。

    这是「同一套枚举分散在三层」的第五次现身（前四次：前端流水线格子、operations 的
    死 operation_type、index_definitions 空壳、生命周期事件声明 11 种只接 4 种）。
    数据库是唯一真相源，其余两层跟着它走。
    """

    import os
    import re
    import typing

    import psycopg

    from backend.app.schemas import IndexVersionResponse

    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("需要 PostgreSQL")

    from backend.app.database import apply_migrations

    apply_migrations(os.environ["TEST_DATABASE_URL"])
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        definition = connection.execute(
            """SELECT pg_get_constraintdef(con.oid)
               FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid
               WHERE con.contype='c' AND rel.relname='index_versions'
                 AND pg_get_constraintdef(con.oid) LIKE '%status%ANY (ARRAY%'"""
        ).fetchone()[0]
    database_values = set(re.findall(r"'([a-z_]+)'::text", definition))

    annotation = IndexVersionResponse.model_fields["status"].annotation
    response_values = set(typing.get_args(annotation))

    assert response_values == database_values, (
        f"响应模型缺少 {sorted(database_values - response_values)}，"
        f"多出 {sorted(response_values - database_values)}——"
        "缺失的那些一旦在库里出现，整个接口会 500"
    )


def test_successful_build_finishes_before_version_validation() -> None:
    """构建完成后 Build/Operation 必须终止，Version 才进入独立验证阶段。

    这条用例捕获的回归是把 ``Version=validating`` 当成构建失败，或让构建任务一直
    停在 ready/validate 等待另一个领域动作。三者应该分别表达事实，不能互相代替。
    """

    from backend.app.pipeline_governance import (
        aggregate_index_build,
        ensure_index_build,
        upsert_document_index_state,
    )

    database_url = _database_url()
    _reset(database_url)
    document_version_id = _add_document(database_url, "alpha")
    index_version_id, _ = _build(database_url, "rbd_build_terminal")
    _cover(database_url, index_version_id, document_version_id)

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        index_build_id = ensure_index_build(
            connection,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            index_version_id=index_version_id,
        )
        upsert_document_index_state(
            connection,
            index_build_id=index_build_id,
            index_version_id=index_version_id,
            document_id="doc_alpha",
            document_version_id=document_version_id,
            status="ready",
        )

    aggregate_index_build(database_url, "rbd_build_terminal")

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        build = connection.execute(
            """SELECT ib.status, o.status AS operation_status, o.current_stage
               FROM index_builds ib JOIN operations o USING (operation_id)
               WHERE ib.index_build_id=%s""",
            (index_build_id,),
        ).fetchone()
        version_status = connection.execute(
            "SELECT status FROM index_versions WHERE index_version_id=%s",
            (index_version_id,),
        ).fetchone()["status"]

    assert dict(build) == {
        "status": "succeeded",
        "operation_status": "succeeded",
        "current_stage": "complete",
    }
    assert version_status == "validating"


def test_partially_failed_build_keeps_its_execution_outcome() -> None:
    """Version 构建失败不能把 Build/Operation 的 partial_failed 证据抹成 failed。"""

    from backend.app.pipeline_governance import (
        aggregate_index_build,
        ensure_index_build,
        upsert_document_index_state,
    )

    database_url = _database_url()
    _reset(database_url)
    alpha = _add_document(database_url, "alpha")
    beta = _add_document(database_url, "beta")
    index_version_id, _ = _build(database_url, "rbd_build_partial")
    _cover(database_url, index_version_id, alpha)

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        index_build_id = ensure_index_build(
            connection,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            index_version_id=index_version_id,
        )
        upsert_document_index_state(
            connection,
            index_build_id=index_build_id,
            index_version_id=index_version_id,
            document_id="doc_alpha",
            document_version_id=alpha,
            status="ready",
        )
        upsert_document_index_state(
            connection,
            index_build_id=index_build_id,
            index_version_id=index_version_id,
            document_id="doc_beta",
            document_version_id=beta,
            status="failed",
        )

    aggregate_index_build(database_url, "rbd_build_partial")

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        build = connection.execute(
            """SELECT ib.status, o.status AS operation_status
               FROM index_builds ib JOIN operations o USING (operation_id)
               WHERE ib.index_build_id=%s""",
            (index_build_id,),
        ).fetchone()
        version_status = connection.execute(
            "SELECT status FROM index_versions WHERE index_version_id=%s",
            (index_version_id,),
        ).fetchone()["status"]

    assert dict(build) == {
        "status": "partial_failed",
        "operation_status": "partial_failed",
    }
    assert version_status == "build_failed"


@pytest.mark.parametrize("failed_status", ["build_failed", "validation_failed"])
def test_failed_versions_can_be_cleaned(failed_status: str) -> None:
    """状态拆分后两类失败版本都必须拥有清理出口。"""

    from backend.app.index_versions import cleanup_version

    database_url = _database_url()
    _reset(database_url)
    document_version_id = _add_document(database_url, "alpha")
    index_version_id, _ = _build(database_url)
    _cover(database_url, index_version_id, document_version_id)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE index_versions SET status=%s WHERE index_version_id=%s",
            (failed_status, index_version_id),
        )

    assert cleanup_version(database_url, index_version_id) == 2
    with psycopg.connect(database_url) as connection:
        status = connection.execute(
            "SELECT status FROM index_versions WHERE index_version_id=%s",
            (index_version_id,),
        ).fetchone()[0]
    assert status == "cleaned"
