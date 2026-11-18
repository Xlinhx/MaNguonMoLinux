from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    project_root: Path
    runtime_dir: Path
    db_path: Path
    nginx_log_path: Path
    snapshots_dir: Path
    nginx_url: str
    test_service_url: str
    test_service_host_url: str
    docker_network: str
    worker_image: str
    api_host: str
    api_port: int
    policy_version: str = "sandbox-policy-v1"

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.getenv("SANDBOX_LAB_ROOT", str(_default_root()))).resolve()
        runtime_dir = Path(os.getenv("SANDBOX_LAB_RUNTIME_DIR", str(root / "runtime"))).resolve()
        db_path = Path(os.getenv("SANDBOX_LAB_DB_PATH", str(runtime_dir / "db" / "sandbox_lab.db"))).resolve()
        nginx_log_path = Path(
            os.getenv("SANDBOX_LAB_NGINX_LOG_PATH", str(runtime_dir / "nginx-logs" / "access.jsonl"))
        ).resolve()
        snapshots_dir = Path(os.getenv("SANDBOX_LAB_SNAPSHOTS_DIR", str(runtime_dir / "snapshots"))).resolve()
        return cls(
            project_root=root,
            runtime_dir=runtime_dir,
            db_path=db_path,
            nginx_log_path=nginx_log_path,
            snapshots_dir=snapshots_dir,
            nginx_url=os.getenv("SANDBOX_LAB_NGINX_URL", "http://127.0.0.1:18080").rstrip("/"),
            test_service_url=os.getenv(
                "SANDBOX_LAB_TEST_SERVICE_URL", "http://sandbox-lab-test-service:8081/ping"
            ),
            test_service_host_url=os.getenv(
                "SANDBOX_LAB_TEST_SERVICE_HOST_URL", "http://127.0.0.1:18081/ping"
            ),
            docker_network=os.getenv("SANDBOX_LAB_DOCKER_NETWORK", "sandbox_lab_net"),
            worker_image=os.getenv("SANDBOX_LAB_WORKER_IMAGE", "sandbox-lab-worker:v1"),
            api_host=os.getenv("SANDBOX_LAB_API_HOST", "127.0.0.1"),
            api_port=int(os.getenv("SANDBOX_LAB_API_PORT", "18000")),
        )

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.nginx_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
