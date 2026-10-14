"""启动器：中文路径在这里解析，bat 只做 ASCII 跳板。

cmd 按 ANSI 码页解析 .bat，写中文进去会解析错乱。所以卦名目录（乾/震/巽/坤）
不出现在 .bat 里，由本文件算出来。

用法（由 震\启动.bat / 震\启动WebUI.bat 调）：
    python launch.py reshaper   # 只跑转发
    python launch.py webui      # 跑 Web 控制台（含转发）
"""
import os
import runpy
import sys
import threading
import webbrowser

# __file__ = <根>\震\launch.py
HERE = os.path.dirname(os.path.abspath(__file__))     # 震
ROOT = os.path.dirname(HERE)                          # 项目根
RUN = os.path.join(ROOT, "乾", "2运势")                # 运行代码
CFG = os.path.join(RUN, "reshape.json")
OPS = os.path.join(ROOT, "巽")                        # 运维工具/日志

sys.path.insert(0, RUN)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def banner(mode):
    print("=" * 60)
    print("  KingIOServer MQTT 报文转换")
    print()
    print("  模式   :", mode)
    print("  代码   :", RUN)
    print("  配置   :", CFG)
    if mode == "Web 控制台":
        print("  浏览器 : http://127.0.0.1:8090/")
    print("  关掉本窗口即停止")
    print("=" * 60)
    print()


def _open_browser(url):
    try:
        webbrowser.open(url)
    except Exception as e:
        print("[提示] 浏览器没自动打开，请手动访问 %s  (%s)" % (url, e))


def main():
    if not os.path.isdir(RUN):
        print("[ERROR] 找不到运行目录:", RUN)
        return 1
    if not os.path.exists(CFG):
        print("[ERROR] 找不到配置文件:", CFG)
        return 1

    mode = (sys.argv[1] if len(sys.argv) > 1 else "webui").strip().lower()
    if mode == "reshaper":
        banner("仅转发")
        sys.argv = ["kio_reshaper.py", CFG]
        runpy.run_path(os.path.join(RUN, "kio_reshaper.py"), run_name="__main__")
    else:
        banner("Web 控制台")
        port = sys.argv[2] if len(sys.argv) > 2 else "8090"
        url = "http://127.0.0.1:%s/" % port
        # 浏览器由 Python 拉，不要让 .bat 去 start：
        #   1) cmd 的 start 在嵌套引号里会报「拒绝访问」
        #   2) 服务还没 bind 就开浏览器，会先看到「连不上」
        # 延迟 1.5 秒等服务就绪。
        threading.Timer(1.5, _open_browser, args=(url,)).start()
        sys.argv = ["kio_webui.py", port, CFG]
        runpy.run_path(os.path.join(RUN, "kio_webui.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
