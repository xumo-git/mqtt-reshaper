"""转发环检测 —— 只提示不拦。

把每个「broker 主题」当节点、每条方案的 监听→目标 当边，有环就是消息会绕回来。
节点按 host:port+主题 判重，不按 broker 名字（同台 broker 可能有多个名字）。
"""
import os
import sys

RUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "2运势")
sys.path.insert(0, RUN)
sys.stdout.reconfigure(encoding="utf-8")

from kio_webui import detect_loops   # noqa: E402

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print("  PASS  %s" % label)
    else:
        fail += 1
        print("  FAIL  %s\n          got  %r\n          want %r" % (label, got, want))


B = {"本地": {"host": "127.0.0.1", "port": 1883},
     "远程": {"host": "10.0.0.2", "port": 1883}}


def cfg(*routes):
    return {"brokers": B, "routes": list(routes)}


def rt(name, src_topic, dst_broker, dst_topic, enabled=None):
    r = {"name": name, "from": {"broker": "本地", "topics": [src_topic]},
         "targets": [{"broker": dst_broker, "topic": dst_topic}]}
    if enabled is not None:
        r["enabled"] = enabled
    return r


print("== 无环 ==")
check("单向 A→B 不算环", detect_loops(cfg(rt("甲", "A", "本地", "B"))), [])
check("两条独立链不算环",
      detect_loops(cfg(rt("甲", "A", "本地", "B"), rt("乙", "C", "本地", "D"))), [])
check("空配置安全", detect_loops({}), [])

print("\n== 直接环 ==")
loops = detect_loops(cfg(rt("甲", "A", "本地", "B"), rt("乙", "B", "本地", "A")))
check("A→B→A 被抓到", len(loops), 1)
check("环回到起点", loops[0]["nodes"][0], loops[0]["nodes"][-1])
check("步骤标出方案名", "甲" in loops[0]["steps"][0] and "乙" in loops[0]["steps"][1], True)

print("\n== 多方环 ==")
loops = detect_loops(cfg(rt("甲", "A", "本地", "B"),
                         rt("乙", "B", "本地", "C"),
                         rt("丙", "C", "本地", "A")))
check("A→B→C→A 被抓到", len(loops) >= 1, True)
check("环里有 3 个节点", len(loops[0]["nodes"]), 4)   # 回到起点，4 个点

print("\n== 跨 broker 也要认 ==")
loops = detect_loops(cfg(rt("甲", "A", "远程", "A"), rt("乙", "A", "本地", "A")))
check("本地/远程同主题也算环", len(loops), 1)

print("\n== 节点按 host:port 判重，不按名字 ==")
# 两个名字指同一台 broker，名字不同但 IP 相同 -> 仍是环
b2 = dict(B, **{"别名": {"host": "127.0.0.1", "port": 1883}})
c = {"brokers": b2,
     "routes": [{"name": "甲", "from": {"broker": "本地", "topics": ["A"]},
                 "targets": [{"broker": "别名", "topic": "B"}]},
                {"name": "乙", "from": {"broker": "别名", "topics": ["B"]},
                 "targets": [{"broker": "本地", "topic": "A"}]}]}
check("同 IP 不同名仍是环", len(detect_loops(c)), 1)

print("\n== 停用的方案不参与 ==")
check("enabled=false 的边不算",
      detect_loops(cfg(rt("甲", "A", "本地", "B"), rt("乙", "B", "本地", "A", enabled=False))), [])

print("\n== 边角 ==")
check("缺 targets 不炸", detect_loops({"brokers": B,
      "routes": [{"name": "甲", "from": {"broker": "本地", "topics": ["A"]}}]}), [])

loops = detect_loops(cfg(rt("甲", "A", "本地", "A")))
check("自环 A→A 被抓到", len(loops), 1)
check("自环回到起点", loops[0]["nodes"][0], loops[0]["nodes"][-1])

multi = {"brokers": B,
         "routes": [
             {"name": "甲", "from": {"broker": "本地", "topics": ["A", "B"]},
              "targets": [{"broker": "本地", "topic": "C"}]},
             {"name": "乙", "from": {"broker": "本地", "topics": ["C"]},
              "targets": [{"broker": "本地", "topic": "A"}]},
         ]}
check("多监听主题都算边（A→C→A 成环）", len(detect_loops(multi)), 1)

print("\n%d 项,失败 %d" % (ok + fail, fail))
sys.exit(1 if fail else 0)
