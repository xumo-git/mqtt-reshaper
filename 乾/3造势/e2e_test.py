"""Watch the raw topic and the reshaped output topic side by side to verify end-to-end."""
import json
import os
import sys
import time
from datetime import datetime

RUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "2运势")
sys.path.insert(0, RUN)
from mqtt_mini import MiniMQTT

RAW = "kingscada"
OUT = "MES"
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0

rows = []


def on_msg(topic, payload):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    side = "RAW" if topic == RAW else "OUT"
    try:
        body = json.dumps(json.loads(payload.decode("utf-8")), ensure_ascii=False)
    except Exception:
        body = repr(payload[:160])
    rows.append((ts, side, topic, body))
    print(f"[{ts}] {side:3s} {topic:10s} {body}")
    sys.stdout.flush()


c = MiniMQTT(client_id="e2e_watcher", username="kingscada", password="Aa@1165051755")
c.on_message = on_msg
c.connect()
c.subscribe(RAW)
c.subscribe(OUT)
print(f"== watching '{RAW}' and '{OUT}' for {DURATION:.0f}s ==")
print("   (waiting for KingIOServer to publish ...)\n")
sys.stdout.flush()
time.sleep(DURATION)
c.disconnect()

raw = [r for r in rows if r[1] == "RAW"]
out = [r for r in rows if r[1] == "OUT"]
print(f"\n{'='*70}")
print(f"RAW  {RAW}: {len(raw)} message(s)")
print(f"OUT  {OUT}: {len(out)} message(s)")
if raw and out:
    print("\n结论: 有原始报文进来,并且转换后的报文已经发到 MES")
elif raw and not out:
    print("\n结论: 收到了原始报文,但 MES 上没有输出 -> 转换规则没匹配上,检查 reshape.json")
elif not raw and out:
    print("\n结论: 只有输出没有输入,不对劲")
else:
    print("\n结论: 这段时间没有流量。IOServer 是按变化发布,可以手动触发 PUB0.0 或改个值再看")
