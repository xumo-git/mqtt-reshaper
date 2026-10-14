"""Verify the UI can tell whether a broker has a password without receiving the password."""
import os
import json
import urllib.request

# __file__ = <根>\乾造势\<本文件>；reshape.json 和模块都在 乾/2运势
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


cfg = call("/api/config")
print("1) /api/config 返回的 broker 字段:")
for name, b in cfg["brokers"].items():
    print("   %-12s has_password=%s  含 password 字段? %s"
          % (name, b.get("has_password"), "password" in b))
raw = json.dumps(cfg, ensure_ascii=False)
print("   响应里含密码明文?", "Aa@1165051755" in raw)

print("\n2) 按前端逻辑保存回去(不带 password 字段)")
body = cfg["config"]
for b in (body.get("brokers") or {}).values():
    if isinstance(b, dict):
        b.pop("has_password", None)
call("/api/config", {"config": body, "restart": False})

disk = json.load(open(os.path.join(RUN, "reshape.json"), encoding="utf-8"))
b0 = list(disk["brokers"].values())[0]
print("   磁盘 brokers 里有 password?", "password" in b0, "->", ("***" if b0.get("password") else None))
print("   磁盘里有 has_password 残留?", "has_password" in b0)
print("   磁盘 routes.from 有 password?", "password" in disk["routes"][0]["from"])

print("\n3) 再读一次(模拟刷新)")
cfg2 = call("/api/config")
for name, b in cfg2["brokers"].items():
    print("   %-12s has_password=%s  ->  列表显示 %s"
          % (name, b.get("has_password"),
             "已设密码" if b.get("has_password") else "无密码"))
