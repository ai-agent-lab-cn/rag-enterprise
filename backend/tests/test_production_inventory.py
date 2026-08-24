from scripts.production_inventory import assess


def test_inventory_assessment_matches_migration_counts_without_secrets() -> None:
    counts = {
        "users": 2,
        "knowledge_bases": 1,
        "memberships": 2,
        "sessions": 0,
        "data_sources": 1,
        "documents": 1,
        "document_versions": 1,
        "chunks": 3,
        "vectors": 3,
        "index_jobs": 0,
    }
    report = assess(
        counts,
        {
            "documents_without_current_ready_version": 0,
            "orphan_memberships": 0,
            "ready_versions_without_chunks": 0,
        },
        {
            "users": 2,
            "knowledge_bases": 1,
            "memberships": 2,
            "sessions": 0,
            "documents": 1,
            "document_versions": 1,
            "chunks": 3,
        },
    )

    assert report["verdict"] == "pass"
    assert report["secrets_included"] is False


def test_inventory_assessment_fails_on_count_or_integrity_difference() -> None:
    report = assess(
        {
            "users": 1,
            "knowledge_bases": 1,
            "memberships": 0,
            "sessions": 1,
            "data_sources": 0,
            "documents": 0,
            "document_versions": 0,
            "chunks": 0,
            "vectors": 0,
            "index_jobs": 0,
        },
        {"orphan_memberships": 1},
        {"users": 2, "sessions": 0},
    )

    assert report["verdict"] == "fail"
    assert report["checks"]["count.users"]["status"] == "fail"
    assert report["checks"]["orphan_memberships"]["status"] == "fail"
