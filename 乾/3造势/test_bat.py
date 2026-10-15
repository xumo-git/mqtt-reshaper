"""bat 文件的两条硬约束：纯 ASCII + CRLF。

cmd 按 ANSI 码页解析 .bat：
  · 内容里有非 ASCII（比如中文）会被拆成多字节乱解析，
    表现是 `'m' 不是内部或外部命令`、`'tle' 不是内部或外部命令` 这种碎片
  · 换行符必须是 CRLF

踩过的坑：改启动脚本时在**注释里**写了中文路径，整份脚本解析全乱。
写了「Keep this file ASCII-only」的文件，自己却违反了它。
"""
import os
import sys

TDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(TDIR))          # 乾 -> 项目根
sys.stdout.reconfigure(encoding="utf-8")

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print("  PASS  %s" % label)
    else:
        fail += 1
        print("  FAIL  %s\n          got  %r\n          want %r" % (label, got, want))


bats = []
for r, d, f in os.walk(ROOT):
    d[:] = [x for x in d if x not in (".git", "__pycache__", "坤")]
    for n in f:
        if n.lower().endswith(".bat"):
            bats.append(os.path.join(r, n))
bats.sort()

print("== 找到 %d 个 .bat ==" % len(bats))
check("至少有一个 bat", len(bats) > 0, True)

for p in bats:
    name = os.path.relpath(p, ROOT)
    b = open(p, "rb").read()
    non = sum(1 for c in b if c > 127)
    crlf = b.count(b"\r\n")
    bare_lf = b.count(b"\n") - crlf

    print()
    print("  %s  (%d 字节)" % (name, len(b)))
    check("  纯 ASCII", non, 0)
    check("  换行是 CRLF", bare_lf, 0)
    # 跳板 bat 不该自己去碰中文路径，中文交给 launch.py
    txt = b.decode("ascii", errors="replace")
    check("  用 %~dp0 而非写死路径", "%~dp0" in txt, True)

print()
print("%d 项,失败 %d" % (ok + fail, fail))
sys.exit(1 if fail else 0)
