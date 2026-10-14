"""把使用说明导出成带图的 Word 文档。"""
import os
import sys

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

sys.stdout.reconfigure(encoding="utf-8")
# __file__ = <根>\乾\3造势\make_docx.py —— 上溯两级到 乾，再到项目根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "使用说明.docx")
IMG = os.path.join(ROOT, "docs")

doc = Document()
st = doc.styles["Normal"]
st.font.name = "微软雅黑"
st.font.size = Pt(10.5)


def h(text, level=1):
    doc.add_heading(text, level=level)


def p(text, bold=False, size=10.5, color=None):
    par = doc.add_paragraph()
    run = par.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    if color:
        run.font.color.rgb = color
    return par


def code(text):
    par = doc.add_paragraph()
    run = par.add_run(text)
    run.font.name = "Consolas"
    run.font.size = Pt(9)
    par.paragraph_format.left_indent = Inches(0.25)
    return par


def bullets(items):
    for it in items:
        doc.add_paragraph(it, style="List Bullet")


def table(headers, rows):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    for i, x in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = x
        for par in cell.paragraphs:
            for r in par.runs:
                r.bold = True
    for row in rows:
        cells = t.add_row().cells
        for i, x in enumerate(row):
            cells[i].text = str(x)
    doc.add_paragraph()


def img(name, width=6.4):
    pth = os.path.join(IMG, name)
    if not os.path.exists(pth):
        # 找不到就喊一声：静默跳过会让「图没嵌进去」这种事查半天
        raise FileNotFoundError("图没找到: %s" % pth)
    doc.add_picture(pth, width=Inches(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER


# ================================================================ 封面
t = doc.add_heading("MQTT 报文转换 / 转发工具", level=0)
p("把 MQTT 上的报文按规则改写成你想要的格式，再转发出去。", bold=True, size=12)
p("不动厂商 DLL，格式全在配置文件里改。", size=11, color=RGBColor(0x55, 0x55, 0x55))
img("arch.png")

# ================================================================
h("一、它解决什么问题", 1)
p("很多工控软件（例如 KingIOServer）通过 MQTT 往外发数据，但发出来的报文格式是厂商定的，你要的格式是另一套。常见三条路：")
table(["做法", "问题"], [
    ["改厂商 DLL", "厂商升级一覆盖就废；只能管一条路；每改一次格式都要重新逆向"],
    ["等厂商开发", "排期不可控；他们按自己的节奏"],
    ["自己做个转换器", "← 就是这个工具"],
])
p("它夹在两个 MQTT 中间：订阅 → 按规则转换 → 转发。格式改坏了改配置就行，不用重新编译、不用碰厂商文件。")
p("一条报文进来，可以扇出成多条（一个变量一条），可以取任意字段、做算术、按规则分流。")
img("rules.png")

# ================================================================
h("二、能干什么", 1)
bullets([
    "规则配置：模板引擎，支持取值、多路回落、毫秒偏移时间戳、用变量名当输出的键",
    "多方案并行：一个监听可绑多个目标，每个目标绑不同规则；也可做双向（A→B 与 B→A 同时跑）",
    "Web 界面：实时数据、配置 broker、规则配置、方案配置、模拟测试、审计追踪",
    "模拟测试：离线试算（不碰 broker）+ 在线真发真收（走完整条链路）",
    "历史数据：报文分段存储、配置的增删改留痕",
    "零第三方依赖：只用 Python 标准库，不用 pip install 任何东西",
])

# ================================================================
h("三、快速开始", 1)
h("1. 环境", 2)
table(["需要", "说明"], [
    ["Python 3.12+", "python.org 下载安装，装的时候勾上 Add to PATH"],
    ["一个 MQTT broker", "EMQX / Mosquitto / HiveMQ 都行，本地或远程都可以"],
    ["Windows", "启动脚本是 .bat；Mac/Linux 用 python 震/launch.py webui"],
])
p("不装任何第三方库。整个工具只用 Python 自带的模块。", bold=True)

h("2. 启动", 2)
p("双击 震\\启动WebUI.bat。稍等两秒，浏览器会自动打开 http://127.0.0.1:8090/ 。关掉那个黑窗口就停止。")
p("命令行等价：")
code("python 震/launch.py webui        # Web 控制台（含转发）")
code("python 震/launch.py reshaper     # 只转发，不开界面")

h("3. 填配置", 2)
p("仓库里带的是示例配置，里面是占位符。复制一份并填上你自己的账号：")
code("""cd 乾/2运势
copy reshape.example.json reshape.json     # Windows
# cp reshape.example.json reshape.json     # Mac / Linux""")
p("打开 reshape.json，把「改成你的用户名」/「改成你的密码」换掉。")
p("reshape.json 含账号密码，已在 .gitignore 里，不会进版本库。想改格式也可以直接编辑它，改完重启服务生效。",
  bold=True)

h("4. 三步配好", 2)
p("① 配置 broker —— 告诉它连谁", bold=True)
code("""名称        比如「本地EMQX」
broker IP   127.0.0.1          ← 只填 IP，端口填下一栏
端口号      1883
用户名/密码  你的账号
主题        kingscada""")
p("点「测试通路」，变绿就通了。然后「保存」。")
p("提示：IP 栏里填 127.0.0.1:1883 也行，会自动把端口拆到下一栏。")

p("② 规则配置 —— 告诉它怎么转", bold=True)
p("点「+ 新增规则」，填三样：匹配条件、扇出、输出字段。第五节有完整例子。")

p("③ 方案配置 —— 把前两步串起来", bold=True)
p("点「+ 新建方案」，选监听哪个 broker、转发到哪个 broker、用哪几条规则，保存并运行。回到「实时数据」页就能看到报文在跑。")

# ================================================================
h("四、界面介绍", 1)
table(["页面", "干什么"], [
    ["实时数据", "左边原始报文、右边转换结果，点左边某一条看它是怎么转出来的。顶上四个计数：收到 / 转发 / 解析失败 / 发布失败"],
    ["配置broker", "存连接信息。这里不区分监听还是目标——同一个 broker 可以在方案甲里当监听、在方案乙里当目标"],
    ["规则配置", "管转换规则。点一行展开看完整内容，可编辑 / 复制 / 删除"],
    ["方案配置", "管转发方案：谁监听、扇出到谁、用哪些规则。可新建 / 运行 / 停止 / 复制"],
    ["模拟测试", "离线转换测试（只算模板）+ 在线转换测试（真发真收）"],
    ["审计追踪", "报文记录 / 动作记录（增删改留痕）/ 存储管理"],
])
p("左上角「MQTT 转发」标题点一下会重启服务，改完连接信息点它生效。", bold=True)

h("核心概念", 2)
code("""broker   =  连接信息：连谁、怎么连
规则     =  转换逻辑：怎么拆、怎么取、输出什么
方案     =  串起来：从哪个 broker 的哪个主题收，按哪几条规则转，发到哪个 broker 的哪个主题""")
p("多个方案可以同时跑。例如双向转换：")
code("""方案1   监听 kingscada  ──规则A──▶  MES
方案2   监听 MES       ──规则B──▶  kingscada""")

# ================================================================
h("五、规则怎么写", 1)
table(["字段", "作用", "必填"], [
    ["规则名称", "方案里按名字勾选它", "是"],
    ["匹配条件", "报文里必须能取到它，规则才生效。留空 = 所有报文都用", ""],
    ["扇出", "对数组/对象逐元素发一条。留空 = 整包一条", ""],
    ["输出字段", "输出的 JSON 长什么样", "是"],
    ["示例报文", "模拟测试时自动填进输入框", ""],
])

h("取值语法", 2)
table(["写法", "含义"], [
    ["@PVs.1", "取 payload[\"PVs\"][\"1\"]，深入报文任意层级"],
    ["@Objs.0.N", "数组按下标取"],
    ["@.", "报文本身，配合「扇出」用"],
    ["@#key", "当前元素的键（扇出对象时）"],
    ["@#value / @#value.N", "当前元素的值 / 它的 .N 字段"],
    ["@#index", "当前下标 / 键名"],
    ["@#now", "当前时间 YYYY-MM-DD HH:MM:SS.mmm"],
    ["@#ts", "有效时间戳 = PVs.2 + 本元素 2 字段的毫秒偏移"],
    ["@a|b|c", "依次回落，取到哪个用哪个"],
    ["其他", "原样输出的字面量"],
])
p("字段名也可以写 @ —— 用变量名直接当输出的键。", bold=True)

h("@#ts 为什么要单独一个", 2)
p("有些软件发的报文里，每个变量的时间戳不是独立字段，而是相对包时间戳的毫秒偏移量：")
code("""{ "PVs": { "2": "2026-10-10 17:04:27.663" },
  "Objs": [ {"N":"pub10", "2":-1626}, {"N":"pub11", "2":-1625} ] }""")
p("pub10 的真实时间是 17:04:26.037，pub11 是 17:04:26.038 —— 逐点扫描，各差 1 毫秒。写 @PVs.2 会把全包变量都打成同一个时刻；写 @#ts 才是每个点位自己的采样时刻。")

h("例一：拆数组，一变量一条", 2)
p("输入", bold=True)
code("""{ "PNs": {"1":"V","2":"T","3":"Q"},
  "PVs": {"1":1616, "2":"2026-10-09 17:16:47.569", "3":192},
  "Objs": [{"N":"pub10"},{"1":0,"N":"pub11"},{"1":15,"N":"pub14"}] }""")
p("规则", bold=True)
table(["项目", "值"], [
    ["匹配条件", "@PNs"],
    ["扇出", "@Objs"],
    ["输出字段名 DataName", "@#value.N"],
    ["输出字段名 DataValue", "@#value.1|PVs.1"],
    ["输出字段名 DataTime", "@#ts"],
    ["输出字段名 DataQuality", "@#value.3|PVs.3"],
])
p("输出（三条）", bold=True)
code("""{"DataName":"pub10","DataValue":1616,"DataTime":"2026-10-09 17:16:47.569","DataQuality":192}
{"DataName":"pub11","DataValue":0,  "DataTime":"2026-10-09 17:16:47.569","DataQuality":192}
{"DataName":"pub14","DataValue":15, "DataTime":"2026-10-09 17:16:47.569","DataQuality":192}""")
p("@#value.1|PVs.1 的意思是：先取对象自己的 1 字段，取不到就用 PVs.1。因为报文里「等于基准值的字段会被省略」，省略了就代表它等于基准值。")

h("例二：扁平报文，逐个键发一条", 2)
p("输入", bold=True)
code('{"tag1": 0, "tag2": 0, "tag3": 0}')
p("规则", bold=True)
table(["项目", "值"], [
    ["匹配条件", "@tag1  （写 @tag1|/tag1 更保险）"],
    ["扇出", "@."],
    ["输出字段名 DataName", "@#key"],
    ["输出字段名 DataValue", "@#value"],
    ["输出字段名 DataTime", "@#now"],
])
p("输出（三条）", bold=True)
code("""{"DataName":"tag1","DataValue":0,"DataTime":"..."}
{"DataName":"tag2","DataValue":0,"DataTime":"..."}
{"DataName":"tag3","DataValue":0,"DataTime":"..."}""")

h("例三：变量名直接当键", 2)
p("输入同例一。扇出 @Objs，输出字段名写 @#value.N，值写 @#value.1|PVs.1。注意字段名本身写了 @，它会从报文里取出来当键。")
p("输出", bold=True)
code("""{"pub10": 1616}
{"pub11": 0}
{"pub14": 15}""")
p("左边是变量名，右边是它的值 —— 一步到位，不用再包一层。")

# ================================================================
h("六、历史数据", 1)
img("storage.png")
p("报文记录分段存，坤/messages/ 下一个段一个文件，文件名 yyyy-mm-dd-序号.db。两个条件谁先到谁触发开新库：")
table(["条件", "默认", "可改"], [
    ["存储上限", "500 MB", "是"],
    ["分段周期", "1 天", "是"],
])
p("动作记录单独一个文件 坤/actions.db，记录配置的每次增删改（broker / 方案 / 规则配置 / 数据库）和连接运行事件。改动是在服务端按名字对出来的，不管从界面哪改都会留痕。")
p("旧数据不自动删，库多了去「审计追踪 → 存储管理」手动删。")
p("为什么分段：单文件方案里 DELETE 不会缩小 SQLite 文件（空页留着复用）。分段之后删旧数据 = 删掉整个 .db 文件，磁盘立刻回来。")

# ================================================================
h("七、常见问题", 1)
faq = [
    ("测试通路报「IP / 域名无法解析」",
     "IP 栏里填成 192.168.1.100:1883 了。端口要填在「端口号」栏。（新版会自动拆开）"),
    ("测试通路报「用户名或密码错误」",
     "点密码框右边的小眼睛看看密码对不对，多半是少打或打错一个字符。也确认用户名大小写。"),
    ("实时数据页一直显示「摸鱼中」",
     "监听主题没收到东西。检查：① 方案是不是点了「运行」；② 监听主题名对不对；③ 那个 broker 上真的有人发吗。"),
    ("转换结果是 0 条",
     "规则的匹配条件没命中这条报文。用「模拟测试 → 离线转换测试」粘贴真实报文试算。"),
    ("改了配置没生效",
     "点左上角「MQTT 转发」标题重启服务。"),
    (".bat 双击闪退 / 报「拒绝访问」",
     "确认装了 Python 并勾了 Add to PATH。脚本里不能写中文和嵌套引号的 start（cmd 按 ANSI 码页解析）。"),
    ("日志在哪",
     "巽/kio_reshaper.log。超过 5 MB 自动轮转，留最近 3 份。"),
    ("能跑在 Linux / Mac 上吗",
     "可以。用 python 震/launch.py webui 启动，.bat 只是 Windows 的跳板。"),
]
for q, a in faq:
    p("Q：" + q, bold=True)
    p("A：" + a)

# ================================================================
h("八、开发者", 1)
p("测试脚本在 乾/3造势/，改完代码跑一遍，防止把以前好的弄坏：")
code("""cd 乾/3造势
python test_direct.py       # 规则转换（含动态字段名）
python test_simtest.py      # 按规则试算
python test_storage.py      # 分段存储
python test_stability.py    # 连接超时 / 死连接 / 包上限
python test_actions.py      # 增删改留痕
python test_ts.py           # @#ts 时间偏移
node check_save.js ../2运势/webui.html   # 前端""")

# ================================================================
h("九、为什么不用改 DLL", 1)
p("项目早期真的动手改过厂商 DLL（反汇编定位字符串 + 指针重定向 + 代码洞跳板），技术上可行，但不划算：")
bullets([
    "只能管一条路，另一条发布路径管不到",
    "每改一次格式都要重新逆向",
    "厂商升级直接覆盖，补丁全废",
    "要处理槽位宽度、寄存器复用等细节",
])
p("结论：格式让配置管，别跟二进制较劲。", bold=True)

doc.add_paragraph()
p("MIT License. 随便用，随便改。有问题提 Issue。", size=9,
  color=RGBColor(0x88, 0x88, 0x88))

doc.save(OUT)
print("输出:", OUT, "%.0f KB" % (os.path.getsize(OUT) / 1024))

# python-docx 有个已知毛病：标题里的括号注释会被重复渲染，回扫一下
from docx import Document as D2
d2 = D2(OUT)
bad = 0
for par in d2.paragraphs:
    t = par.text
    if t.count("（") != t.count("）"):
        print("  !! 括号不配对:", t[:60])
        bad += 1
    for k in ["（（", "））"]:
        if k in t:
            print("  !! 重复括号:", t[:60])
            bad += 1
print("回扫 %d 处异常" % bad)
print("段落 %d · 表格 %d · 图 %d" % (
    len(d2.paragraphs), len(d2.tables),
    len(d2.inline_shapes)))
