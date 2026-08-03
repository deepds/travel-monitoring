import json
from tco.connectors.registry import create_connector
for code in ("tutu_mcp", "rzd"):
    c = create_connector(code)
    r = c.health_check()
    print(f"{code:10} outcome={r.outcome} latency={r.latency_ms}ms err={r.error_code}")
    print(f"           msg={(r.error_message or '')[:150]}")
    print(f"           diag={json.dumps(r.diagnostics, ensure_ascii=False)[:250]}")
