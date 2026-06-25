#!/usr/bin/env python3
"""OPRAI Orchestrator API Server — root/lab entrypoint (thin wrapper)."""

import os

from modules.instance_paths import instance_root_str
from modules.orchestrator_api_core import OrchestratorApiConfig, create_app

app = create_app(
    OrchestratorApiConfig(
        instance_root=instance_root_str(),
        default_port=5004,
        variant="root",
        enable_lab_target=True,
        guard_bridge_on_import=True,
        guard_bridge_on_load_executor=True,
        context_include_workspace_root=True,
    )
)

if __name__ == "__main__":
    print("🚀 Запуск OPRAI Orchestrator API Server...")
    print("📡 Доступно на: http://localhost:8000")
    print("💬 Чат endpoint: POST /api/chat")
    print("🏥 Health check: GET /api/health")

    print("\\n🔍 ЗАРЕГИСТРИРОВАННЫЕ МАРШРУТЫ:")
    for rule in app.url_map.iter_rules():
        if "execute" in rule.rule:
            print(f"   ✅ {rule.rule} -> {rule.methods}")

    port = int(os.getenv("PORT", "5004"))
    print(f"\\n🌐 Запуск на порту: {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
