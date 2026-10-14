"""Verify that saving through the web API actually persists, survives a reload,
and keeps Chinese intact."""
import os
import json
import time
import urllib.request

# reshape.json 和模块都在 运行目录 乾/2运势
RUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "2运势")

BASE = "http://127.0.0.1:8090"


def call(path, payload=None):
    if payload is None:
        req = urllib.request.Request(BASE + path)
    else:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data,
                                     headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


print("1) 当前配置")
cfg = call("/api/config")["config"]
print("   brokers:", list(cfg.get("brokers", {})))
print("   routes :", [r.get("name") for r in cfg.get("routes", [])])

print("\n2) 加一个测试 broker,保存(restart=false)")
cfg.setdefault("brokers", {})["测试目标"] = {
    "role": "target", "host": "10.0.0.9", "port": 1883,
    "username": "tester", "password": "pw123", "topic": "MES",
}
call("/api/config", {"config": cfg, "restart": False})

disk = open(os.path.join(RUN, "reshape.json"), encoding="utf-8").read()
print("   磁盘含「测试目标」?", "测试目标" in disk)
print("   磁盘含「方案一」  ?", "方案一" in disk)

print("\n3) 重新读取(模拟页面刷新)")
cfg2 = call("/api/config")["config"]
print("   brokers:", list(cfg2.get("brokers", {})))
print("   routes :", [r.get("name") for r in cfg2.get("routes", [])])
lib = cfg2.get("rule_library") or {}
first = next(iter(lib.values()), {})
print("   规则数:", len(lib), "  首条模板键:", list((first.get("template") or {}).keys()))

ok = ("测试目标" in cfg2.get("brokers", {})
      and "方案一" in [r.get("name") for r in cfg2.get("routes", [])]
      and "测试目标" in disk)
print("\n结论:", "持久化正常,刷新不丢,中文完好" if ok else "有问题")

print("\n4) 清理测试数据")
del cfg2["brokers"]["测试目标"]
call("/api/config", {"config": cfg2, "restart": False})
time.sleep(0.3)
cfg3 = call("/api/config")["config"]
print("   清理后 brokers:", list(cfg3.get("brokers", {})))
