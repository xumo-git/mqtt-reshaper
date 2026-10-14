"""生成 README 用的示意图（中文字体，输出到 docs/）。"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

sys.stdout.reconfigure(encoding="utf-8")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

# __file__ = <根>\乾\3造势\make_docs_img.py —— 上溯两级到 乾，再到项目根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "docs")
os.makedirs(OUT, exist_ok=True)

C_BG = "#0e1626"
C_BOX = "#16273b"
C_LINE = "#2b3f5c"
C_ACC = "#00e5ff"
C_OK = "#56d364"
C_WARN = "#e3b341"
C_TXT = "#e6edf3"
C_DIM = "#8b9cb3"


def newfig(w, h):
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor(C_BG)
    ax.set_facecolor(C_BG)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    return fig, ax


def box(ax, x, y, w, h, title, sub="", edge=C_LINE, fill=C_BOX, tc=C_TXT):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle="round,pad=1.2,rounding_size=2",
                                linewidth=1.6, edgecolor=edge, facecolor=fill))
    if sub:
        ax.text(x + w / 2, y + h / 2 + 3.2, title, ha="center", va="center",
                fontsize=13, color=tc, fontweight="bold")
        ax.text(x + w / 2, y + h / 2 - 3.2, sub, ha="center", va="center",
                fontsize=10, color=C_DIM)
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center",
                fontsize=13, color=tc, fontweight="bold")


def panel(ax, x, y, w, h, title, edge=C_LINE, fill=C_BOX):
    """标题在左上角的面板，下面留白给内容，不跟正文抢位置。"""
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle="round,pad=1.2,rounding_size=2",
                                linewidth=1.6, edgecolor=edge, facecolor=fill))
    ax.text(x + 4, y + h - 6, title, fontsize=13, color=C_TXT, fontweight="bold")


def arrow(ax, x1, y1, x2, y2, label="", color=C_ACC, rad=0.0, ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2),
                                 arrowstyle="-|>", mutation_scale=18,
                                 linewidth=1.8, color=color, linestyle=ls,
                                 connectionstyle=f"arc3,rad={rad}"))
    if label:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 4, label,
                ha="center", va="center", fontsize=10, color=color)


# ---------------------------------------------------------------- 图1 数据流
fig, ax = newfig(11, 5.6)
ax.text(2, 96, "数据流", fontsize=16, color=C_TXT, fontweight="bold")
ax.text(2, 90.5, "厂商 DLL 一行不动，格式全在配置里改", fontsize=11, color=C_DIM)

# 上排：四个盒子等距排开，中间留够缝放箭头标签
X = [2, 27, 52, 77]
W = 18
box(ax, X[0], 52, W, 26, "KingIOServer", "发原始报文\n（不动它的 DLL）", edge=C_WARN)
box(ax, X[1], 52, W, 26, "MQTT Broker A", "收原始报文\nEMQX / Mosquitto …")
box(ax, X[2], 52, W, 26, "本工具", "按规则转换\n扇出 · 取值 · 时间偏移", edge=C_ACC)
box(ax, X[3], 52, W, 26, "MQTT Broker B", "可以是同一台\n也可以是另一台")

for i, lab in enumerate(["MQTT", "订阅", "转发"]):
    arrow(ax, X[i] + W + 0.5, 65, X[i + 1] - 0.5, 65)
    ax.text((X[i] + W + X[i + 1]) / 2, 72, lab,
            ha="center", fontsize=10, color=C_ACC)

# 中排：本地存储
box(ax, X[2], 18, W, 22, "本地存储", "报文分段库\n动作留痕（增删改）", edge=C_OK)
arrow(ax, X[2] + W / 2, 51, X[2] + W / 2, 41, color=C_OK)
ax.text(X[2] + W / 2 + 2, 46, "落盘", fontsize=9.5, color=C_OK)

# 下排左：Web 界面
box(ax, X[0], 18, W, 22, "Web 界面", "实时数据 · 规则配置\n模拟测试 · 审计追踪")
ax.add_patch(FancyArrowPatch((X[0] + W + 1, 29), (X[2] - 1, 29),
                             arrowstyle="-|>", mutation_scale=16,
                             linewidth=1.5, color=C_DIM, linestyle="--"))
ax.text((X[0] + W + X[2]) / 2, 35, "配置 / 查看（不走数据面）",
        ha="center", fontsize=9.5, color=C_DIM)

fig.savefig(os.path.join(OUT, "arch.png"), dpi=170, facecolor=C_BG,
            bbox_inches="tight", pad_inches=0.25)
plt.close(fig)
print("  docs/arch.png")

# ---------------------------------------------------------------- 图2 规则转换
fig, ax = newfig(11, 6.2)
ax.text(2, 95, "规则怎么转", fontsize=16, color=C_TXT, fontweight="bold")
ax.text(2, 89, "一条报文进来，按规则扇出成多条", fontsize=11, color=C_DIM)

ax.add_patch(FancyBboxPatch((3, 52), 94, 30,
                            boxstyle="round,pad=1,rounding_size=2",
                            linewidth=1.6, edgecolor=C_LINE, facecolor=C_BOX))
ax.text(6, 78, "输入  一条报文", fontsize=12, color=C_WARN, fontweight="bold")
ax.text(6, 70, '{"PNs":{...},"PVs":{"1":1616,"2":"2026-10-09 17:16:47.569","3":192},',
        fontsize=10.5, color=C_TXT, family="monospace")
ax.text(6, 64, ' "Objs":[{"N":"pub10"},{"1":0,"N":"pub11"},{"1":15,"N":"pub14"}]}',
        fontsize=10.5, color=C_TXT, family="monospace")
ax.text(6, 56, "每个对象自带 1/2/3：1=值  2=相对 PVs.2 的毫秒偏移  3=质量",
        fontsize=9.5, color=C_DIM)

arrow(ax, 50, 52, 50, 43, "规则：扇出 Objs，逐个取字段", color=C_ACC)

ax.add_patch(FancyBboxPatch((3, 8), 94, 33,
                            boxstyle="round,pad=1,rounding_size=2",
                            linewidth=1.6, edgecolor=C_OK, facecolor=C_BOX))
ax.text(6, 37, "输出  一个对象一条", fontsize=12, color=C_OK, fontweight="bold")
lines = [
    ('{"DataName":"pub10","DataValue":1616,"DataTime":"2026-10-09 17:16:45.943"}', C_TXT),
    ('{"DataName":"pub11","DataValue":0,  "DataTime":"2026-10-09 17:16:45.944"}', C_TXT),
    ('{"DataName":"pub14","DataValue":15, "DataTime":"2026-10-09 17:16:45.945"}', C_TXT),
]
y = 29
for t, c in lines:
    ax.text(6, y, t, fontsize=10.5, color=c, family="monospace")
    y -= 5
ax.text(6, 11, "时间戳 = PVs.2 + 该对象的「2」字段毫秒偏移 → 每个点位是自己的采样时刻（不是全包同一时刻）",
        fontsize=9.5, color=C_DIM)

fig.savefig(os.path.join(OUT, "rules.png"), dpi=170, facecolor=C_BG,
            bbox_inches="tight", pad_inches=0.25)
plt.close(fig)
print("  docs/rules.png")

# ---------------------------------------------------------------- 图3 存储
fig, ax = newfig(11, 5.2)
ax.text(2, 96, "历史数据", fontsize=16, color=C_TXT, fontweight="bold")
ax.text(2, 90.5, "报文分段存，动作单独存", fontsize=11, color=C_DIM)

panel(ax, 3, 40, 45, 36, "报文记录   data/messages/", edge=C_ACC)
for i, (nm, note) in enumerate([("2026-10-10-01.db", "归档段"),
                                ("2026-10-13-01.db", "当前段 · 写入中"),
                                ("2026-10-13-02.db", "写满 500MB 开新的")]):
    y = 62 - i * 8
    ax.text(7, y, nm, fontsize=10.5, color=C_TXT, family="monospace")
    ax.text(32, y, note, fontsize=9.5, color=C_OK if i == 1 else C_DIM)

panel(ax, 52, 40, 45, 36, "动作记录   data/actions.db", edge=C_WARN)
ax.text(56, 62, "配置的增删改留痕", fontsize=10.5, color=C_TXT)
ax.text(56, 54, "broker / 方案 / 规则配置 / 数据库", fontsize=9.5, color=C_DIM)
ax.text(56, 46, "连接 / 断开 / 启动 / 失败", fontsize=9.5, color=C_DIM)

panel(ax, 3, 8, 94, 22, "分段的好处")
ax.text(7, 18, "删旧数据 = 删掉整个 .db 文件，磁盘立刻回来", fontsize=10.5, color=C_TXT)
ax.text(7, 11, "（单文件方案里 DELETE 不会缩小 SQLite 文件，只能靠 VACUUM 硬压）",
        fontsize=9.5, color=C_DIM)

fig.savefig(os.path.join(OUT, "storage.png"), dpi=170, facecolor=C_BG,
            bbox_inches="tight", pad_inches=0.25)
plt.close(fig)
print("  docs/storage.png")

for f in sorted(os.listdir(OUT)):
    p = os.path.join(OUT, f)
    print("   %-14s %.0f KB" % (f, os.path.getsize(p) / 1024))
