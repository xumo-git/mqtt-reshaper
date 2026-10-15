"""把 mqtt 转发项目复制成一套「按编号读」的学习版。

原项目不动。学习版是扁平的、带编号的、文件名是
    {编号}_{原英文名称}_{中文名称}_{类型}.{扩展名}
编号从核心到边缘：核心 → 服务 → 界面 → 入口 → 运维 → 测试 → 工具 → 文档
"""
import os
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8")
SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DST = os.path.join(os.path.dirname(SRC), "mqtt转发_学习版")

# (编号, 原相对路径, 中文名, 类型)
PLAN = [
    # ── 核心 ──────────────────────────────────────────────
    ("01", r"乾\2运势\kio_reshaper.py",    "报文转换核心",      "核心"),
    ("02", r"乾\2运势\reshape.json",       "转换配置",          "配置"),
    # ── 服务 ──────────────────────────────────────────────
    ("03", r"乾\2运势\kio_webui.py",       "网页服务端",        "服务"),
    ("04", r"乾\2运势\kio_store.py",       "数据存储",          "服务"),
    ("05", r"乾\2运势\kio_autodiscover.py","自动发现配置",      "服务"),
    # ── 界面 ──────────────────────────────────────────────
    ("06", r"乾\2运势\webui.html",         "网页界面",          "界面"),
    # ── 入口 ──────────────────────────────────────────────
    ("07", r"震\launch.py",                "启动器",            "入口"),
    ("08", r"震\启动WebUI.bat",            "启动控制台",        "入口"),
    ("09", r"震\启动.bat",                 "仅转发启动",        "入口"),
    ("10", r"乾\2运势\mqtt_mini.py",       "迷你MQTT客户端",    "工具"),
    # ── 运维 ──────────────────────────────────────────────
    ("11", r"巽\status.py",                "状态自检",          "运维"),
    ("12", r"巽\stop.py",                  "停止服务",          "运维"),
    ("13", r"巽\状态.bat",                 "状态自检脚本",      "运维"),
    ("14", r"巽\停止.bat",                 "停止服务脚本",      "运维"),
    # ── 测试 ──────────────────────────────────────────────
    ("21", r"乾\3造势\test_bat.py",        "批处理约束",        "测试"),
    ("22", r"乾\3造势\test_loops.py",      "转发环检测",        "测试"),
    ("23", r"乾\3造势\test_simtest.py",    "按规则试算",        "测试"),
    ("24", r"乾\3造势\test_direct.py",     "规则转换",          "测试"),
    ("25", r"乾\3造势\test_stability.py",  "连接健壮性",        "测试"),
    ("26", r"乾\3造势\test_actions.py",    "增删改留痕",        "测试"),
    ("27", r"乾\3造势\test_storage.py",    "分段存储",          "测试"),
    ("28", r"乾\3造势\test_ts.py",         "时间偏移",          "测试"),
    ("29", r"乾\3造势\test_ping.py",       "连通测试",          "测试"),
    ("30", r"乾\3造势\test_persist.py",    "配置持久化",        "测试"),
    ("31", r"乾\3造势\test_pwd.py",        "密码不外泄",        "测试"),
    ("32", r"乾\3造势\test_size.py",       "容量与切段",        "测试"),
    ("33", r"乾\3造势\test_reshaper.py",   "真机转换",          "测试"),
    ("34", r"乾\3造势\e2e_test.py",        "端到端",            "测试"),
    ("35", r"乾\3造势\stress_test.py",     "压力测试",          "测试"),
    ("36", r"乾\3造势\check_save.js",      "前端保存与规则UI",  "测试"),
    ("37", r"乾\3造势\checkjs.js",         "JS语法检查",        "测试"),
    ("38", r"乾\3造势\checkhtml.js",       "HTML结构检查",      "测试"),
    ("39", r"乾\3造势\smoke.js",           "渲染冒烟",          "测试"),
    # ── 工具 ──────────────────────────────────────────────
    ("41", r"巽\db_size.py",               "库大小统计",        "工具"),
    ("42", r"巽\gap_audit.py",             "缺口审计",          "工具"),
    ("43", r"巽\_avcheck.py",              "自检",              "工具"),
    ("44", r"巽\_fix_mqttx.py",            "MQTTX密码修复",     "工具"),
    # ── 文档 ──────────────────────────────────────────────
    ("51", r"乾\4凝势\README.md",          "项目说明",          "文档"),
    ("52", r"乾\3造势\make_docs_img.py",   "示意图生成",        "工具"),
    ("53", r"乾\3造势\make_docx.py",       "Word文档生成",      "工具"),
]


def main():
    if os.path.exists(DST):
        shutil.rmtree(DST)
    os.makedirs(DST)

    print("输出目录:", DST)
    print()
    last_type = None
    for num, rel, cn, typ in PLAN:
        src = os.path.join(SRC, rel.replace("\\", os.sep))
        if not os.path.exists(src):
            print("  !! 缺文件:", rel)
            continue
        ext = os.path.splitext(src)[1]
        base = os.path.splitext(os.path.basename(src))[0]
        dst = os.path.join(DST, "%s_%s_%s_%s%s" % (num, base, cn, typ, ext))
        shutil.copy2(src, dst)
        if typ != last_type:
            print("  ── %s ──" % typ)
            last_type = typ
        print("   %s  ->  %s" % (rel, os.path.basename(dst)))

    print()
    print("共", len([x for x in os.listdir(DST)]), "个文件")


if __name__ == "__main__":
    main()
