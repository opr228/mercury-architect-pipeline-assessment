#!/usr/bin/env python3
"""OPRAI Orchestrator API Server — ORK entrypoint (thin wrapper)."""

import os
import sys

_ORK_ROOT = os.path.dirname(os.path.abspath(__file__))
_LAB_ROOT = os.path.dirname(_ORK_ROOT)
for _path in (_LAB_ROOT, _ORK_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from modules.instance_paths import LAB_ROOT, instance_root_str
from modules.orchestrator_api_core import OrchestratorApiConfig, create_app


def _is_lab_instance() -> bool:
    """True when OPRAI_INSTANCE_ROOT (or default resolution) points at lab tree."""
    root = instance_root_str().rstrip("/")
    lab_root = str(LAB_ROOT).rstrip("/")
    return root == lab_root or root.endswith("/oprai_lab")


def _ork_config() -> OrchestratorApiConfig:
    """Prod ORK (:5005): fixed_prod_paths=True, instance_root=/home/opr.

    Lab ORK (:20005): fixed_prod_paths=False, instance_root=instance_root_str().
    Env gate: OPRAI_INSTANCE_ROOT=/home/opr/oprai_lab (see setup_oprai_lab_env.sh).
    """
    if _is_lab_instance():
        return OrchestratorApiConfig(
            instance_root=instance_root_str(),
            default_port=5005,
            variant="ork",
            fixed_prod_paths=False,
            auto_plan_direct_import=True,
        )
    return OrchestratorApiConfig(
        instance_root="/home/opr",
        default_port=5005,
        variant="ork",
        fixed_prod_paths=True,
        auto_plan_direct_import=True,
    )


app = create_app(_ork_config())

if __name__ == "__main__":
    print("🚀 Запуск OPRAI Orchestrator API Server...")
    print("📡 Доступно на: http://localhost:8000")
    print("💬 Чат endpoint: POST /api/chat")
    print("🏥 Health check: GET /api/health")

    print("\\n🔍 ЗАРЕГИСТРИРОВАННЫЕ МАРШРУТЫ:")
    for rule in app.url_map.iter_rules():
        if "execute" in rule.rule:
            print(f"   ✅ {rule.rule} -> {rule.methods}")

    port = int(os.getenv("PORT", "5005"))
    print(f"\\n🌐 Запуск на порту: {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
