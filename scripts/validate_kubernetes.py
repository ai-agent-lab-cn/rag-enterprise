from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_ROOT = ROOT / "deploy" / "kubernetes"


def _documents(path: Path) -> list[dict[str, Any]]:
    return [item for item in yaml.safe_load_all(path.read_text()) if isinstance(item, dict)]


def validate(manifest_root: Path = MANIFEST_ROOT) -> list[str]:
    errors: list[str] = []
    paths = sorted(manifest_root.glob("*.yaml"))
    if (manifest_root / "secret.yaml").exists():
        errors.append("不能提交 deploy/kubernetes/secret.yaml")
    if not (manifest_root / "secret.example.yaml").exists():
        errors.append("缺少 Secret 模板")

    documents: list[dict[str, Any]] = []
    for path in paths:
        if path.name == "kustomization.yaml":
            continue
        try:
            documents.extend(_documents(path))
        except yaml.YAMLError as exc:
            errors.append(f"{path.name} YAML 无效：{exc}")

    identities: set[tuple[str, str]] = set()
    for item in documents:
        kind = str(item.get("kind", ""))
        metadata = item.get("metadata") or {}
        name = str(metadata.get("name") or metadata.get("generateName") or "")
        identity = (kind, name)
        if identity in identities:
            errors.append(f"资源重复：{kind}/{name}")
        identities.add(identity)
        if kind != "Namespace" and metadata.get("namespace") != "rag-enterprise":
            errors.append(f"资源未限定 rag-enterprise 命名空间：{kind}/{name}")

    required = {
        ("Namespace", "rag-enterprise"),
        ("ConfigMap", "rag-enterprise-config"),
        ("Cluster", "rag-postgres"),
        ("Deployment", "rag-backend"),
        ("Deployment", "rag-worker"),
        ("Deployment", "rag-frontend"),
        ("PersistentVolumeClaim", "rag-uploads"),
        ("PersistentVolumeClaim", "rag-backups"),
    }
    errors.extend(f"缺少资源：{kind}/{name}" for kind, name in sorted(required - identities))

    clusters = [item for item in documents if item.get("kind") == "Cluster"]
    if clusters:
        spec = clusters[0].get("spec") or {}
        image = str(spec.get("imageName", ""))
        sql = ((spec.get("bootstrap") or {}).get("initdb") or {}).get("postInitApplicationSQL", [])
        if spec.get("instances") != 1:
            errors.append("Docker Desktop 演练集群必须显式使用单实例")
        if "@sha256:" not in image or "standard-trixie" not in image:
            errors.append("PostgreSQL 必须使用含 pgvector 的不可变 standard-trixie 镜像")
        if not any("CREATE EXTENSION IF NOT EXISTS vector" in str(statement) for statement in sql):
            errors.append("PostgreSQL 初始化缺少 vector 扩展")

    for deployment in (item for item in documents if item.get("kind") == "Deployment"):
        name = deployment["metadata"]["name"]
        pod_spec = deployment["spec"]["template"]["spec"]
        containers = pod_spec.get("containers", [])
        pod_security = pod_spec.get("securityContext", {})
        if pod_spec.get("terminationGracePeriodSeconds", 0) < 20:
            errors.append(f"{name} 缺少优雅停机时间")
        if pod_security.get("runAsNonRoot") is not True or not isinstance(
            pod_security.get("runAsUser"), int
        ):
            errors.append(f"{name} 未显式使用非 root 数字 UID")
        for container in containers:
            if container.get("imagePullPolicy") != "Never":
                errors.append(f"{name}/{container.get('name')} 未锁定为 Docker Desktop 本地镜像")
            if not container.get("resources", {}).get("requests") or not container.get("resources", {}).get(
                "limits"
            ):
                errors.append(f"{name}/{container.get('name')} 缺少资源边界")
            security = container.get("securityContext", {})
            if security.get("allowPrivilegeEscalation") is not False:
                errors.append(f"{name}/{container.get('name')} 未禁止权限提升")
        if name in {"rag-backend", "rag-worker", "rag-frontend"}:
            probe_container = containers[0] if containers else {}
            for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
                if probe not in probe_container:
                    errors.append(f"{name} 缺少 {probe}")

    kustomization = yaml.safe_load((manifest_root / "kustomization.yaml").read_text())
    resources = set(kustomization.get("resources", []))
    if "secret.yaml" not in resources:
        errors.append("kustomization 必须引用操作者创建的 secret.yaml")
    if any(item.get("kind") == "CronJob" for item in documents):
        errors.append("本阶段禁止隐式定时备份，备份必须由操作者显式执行")
    restore_jobs = [
        item
        for item in documents
        if item.get("kind") == "Job"
        and (item.get("metadata") or {}).get("labels", {}).get("app.kubernetes.io/component")
        == "restore"
    ]
    if not restore_jobs or "rag-restore-drill-secrets" not in yaml.safe_dump(restore_jobs):
        errors.append("缺少使用临时凭据的隔离恢复 Job")
    policies = [item for item in documents if item.get("kind") == "NetworkPolicy"]
    policy_text = yaml.safe_dump(policies, sort_keys=True)
    if "cnpg-system" not in policy_text or "8000" not in policy_text:
        errors.append("数据库 NetworkPolicy 必须允许 CloudNativePG Operator 读取实例状态")
    return errors


def main() -> None:
    errors = validate()
    if errors:
        raise SystemExit("\n".join(f"- {item}" for item in errors))
    print("Kubernetes 清单边界校验通过")


if __name__ == "__main__":
    main()
