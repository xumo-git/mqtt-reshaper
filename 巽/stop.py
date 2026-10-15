"""停止后台运行的 Web 控制台（配合 震\\启动WebUI.bat 的 --bg 模式）。

后台跑就没有窗口可关了，所以要有个东西能停它。
按端口找进程，不依赖 PID 文件——PID 文件一过期就杀错人。
"""
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
PORT = 8090


def pids_on_port(port):
    """找出监听指定端口的进程 PID。"""
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    found = []
    for line in out.splitlines():
        if "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[1]
        if not local.endswith(":%d" % port):
            continue
        try:
            found.append(int(parts[-1]))
        except ValueError:
            pass
    return sorted(set(found))


def main():
    print("=" * 60)
    print("  停止 MQTT 转发 · Web 控制台")
    print("=" * 60)
    pids = pids_on_port(PORT)
    if not pids:
        print("  端口 %d 上没有进程 —— 服务本来就没在跑" % PORT)
        return 0
    for pid in pids:
        r = subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        ok = r.returncode == 0
        print("  %s  PID %d%s" % ("已停止" if ok else "停不掉", pid,
                                  "" if ok else "  " + (r.stdout or r.stderr).strip()))
    time.sleep(1)
    left = pids_on_port(PORT)
    print()
    if left:
        print("  还有 %d 个进程占着 %d 端口" % (len(left), PORT))
        return 1
    print("  已全部停止")
    print("  想再启动：双击 震\\启动WebUI.bat")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BrokenPipeError, OSError):
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
