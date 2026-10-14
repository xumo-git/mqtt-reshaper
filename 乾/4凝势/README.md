# KingIOServer MQTT 报文格式转换

订阅 KingIOServer 发出的 MQTT 报文,按配置模板改写成自定义格式再转发。
**不修改厂商 DLL**,格式全部由配置文件控制。

```
KingIOServer (kxMQTT.dll / MQTT_Request 驱动)  ← 完全不动
        │  原始报文
        ▼
   source broker      (任何标准 broker: EMQX / Mosquitto / HiveMQ ...)
        │  source.topic
        ▼
   [ kio_reshaper ]   ← 按 rules 模板转格式
        │  destination.topic
        ▼
   destination broker (可与 source 不同服务器)
        ▼
     下游订阅方
```

## 目录结构

按知识工坊Web_V1.0 的八卦分层：**卦名 = 模块角色**，乾内部再按 **1起势/2运势/3造势/4凝势** 分阶段。

```
mqtt转发\
├── 乾\              主体
│   ├── 2运势\        运行代码与配置
│   │   ├── kio_reshaper.py       主程序:MQTT 客户端 + 模板引擎
│   │   ├── kio_webui.py          Web 控制台后端
│   │   ├── kio_store.py          存储层(报文分段 + 动作留痕)
│   │   ├── kio_autodiscover.py   读 IOServer 配置取 broker
│   │   ├── mqtt_mini.py          极简客户端(测试用)
│   │   ├── webui.html            Web 界面
│   │   └── reshape.json          配置:改 broker / 主题 / 格式都在这里
│   ├── 3造势\        工具与测试
│   │   ├── test_*.py / check*.js / smoke.js / stress_test.py
│   │   └── payload*.json / out*.json   样例载荷与输出
│   └── 4凝势\        文档(本文件)
├── 震\              启动入口
│   ├── 启动.bat / 启动WebUI.bat   纯 ASCII 跳板
│   └── launch.py                 解析中文路径,真正启动
├── 巽\              运维工具
│   ├── db_size.py / gap_audit.py / _avcheck.py / _fix_mqttx.py
│   └── kio_reshaper.log          运行日志
└── 坤\              数据
    ├── messages\                 报文,分段 yyyy-mm-dd-NN.db
    └── actions.db                动作记录(增删改留痕)
```

**艮没有** —— 本项目无冗余实例，不建。**1起势 空着不建** —— 数据归坤。

## 运行

**双击 `震\启动WebUI.bat`**（含转发）或 `震\启动.bat`（只转发）。关闭窗口即停止。

命令行手动跑:`python 震\launch.py webui` 或 `python 震\launch.py reshaper`

断线自动重连(指数退避,上限 30s)。

> **`.bat` 必须纯 ASCII** —— cmd 按 ANSI 码页解析 `.bat`,中文会让脚本解析错乱。
> 而卦名目录全是中文,所以 bat 只做跳板,**中文路径由 `震\launch.py` 解析**。
> 中文提示也由 Python 打印(已强制 stdout 为 UTF-8)。

## 配置

源和目标是**两组独立配置**,可以指向不同 broker(跨服务器转发)。

```json
{
  "source": {
    "broker": { "host": "127.0.0.1", "port": 1883,
                "username": "kingscada", "password": "...",
                "client_id": "kio_reshaper_src", "keepalive": 30 },
    "topic": "kingscada",
    "qos": 0
  },
  "destination": {
    "broker": { "host": "127.0.0.1", "port": 1883,
                "username": "kingscada", "password": "...",
                "client_id": "kio_reshaper_dst", "keepalive": 30 },
    "topic": "kingscada/flat",
    "qos": 0,
    "retain": false
  },
  "omit_missing": true,
  "log_file": "kio_reshaper.log",
  "rules": [ { "name": "...", "match": "@...", "each": "@...", "template": { ... } } ]
}
```

| 配置段 | 字段 |
|---|---|
| `source` | `broker{host,port,username,password,client_id,keepalive}` + `topic` + `qos` |
| `destination` | 同上,另有 `topic` / `qos` / `retain` |
| `rules` | 见下 |

两边各有独立连接和自动重连,互不影响。旧的单 `broker` + `input_topic`/`output_topic` 写法仍然兼容。

### 规则字段

| 字段 | 作用 |
|---|---|
| `match` | 可选。路径能解析出值才应用本规则(用于按报文结构分流) |
| `each` | 可选。对数组/对象逐元素发一条消息(扇出) |
| `template` | 输出 JSON 的字段定义 |

### 模板取值语法

| 写法 | 含义 |
|---|---|
| `@PVs.1` | 载荷里 `payload["PVs"]["1"]` |
| `@Objs.0.N` | 数字段索引数组:`payload["Objs"][0]["N"]` |
| `@.` | 载荷本身(配合 `each` 用) |
| `@#key` | 当前元素的键(`each` 遍历对象时) |
| `@#value` | 当前元素的值 |
| `@#index` | 当前下标/键 |
| `@#now` | 当前本地时间 `YYYY-MM-DD HH:MM:SS.mmm` |
| `@#ts` | **当前元素的有效时间戳** = `PVs.2` + 本元素 `2` 字段的毫秒偏移 |
| 其他 | 原样作为字面量输出 |

取值支持 `|` 依次回落:`@#value.1|PVs.1` = 先取元素自己的 `1`,取不到再用 `PVs.1`。

值保持原有 JSON 类型(数字还是数字,字符串还是字符串)。`omit_missing: true` 时取不到的字段直接省略。

### 为什么要 `@#ts`

kxMQTT 的 `Objs` 里,每个变量的**采样时刻**不是独立字段,而是相对包时间戳 `PVs.2` 的**毫秒偏移量**:

```json
{ "PNs": {"1":"V","2":"T","3":"Q"},
  "PVs": {"1":0, "2":"2026-10-10 17:04:27.663", "3":192},
  "Objs": [ {"N":"pub10", "1":0, "2":-1626, "3":0},
            {"N":"pub11", "1":0, "2":-1625, "3":0} ] }
```

`pub10` 的真实时间是 `17:04:26.037`,`pub11` 是 `17:04:26.038` —— 逐点扫描,各差 1ms。
直接取 `PVs.2` 会把全包变量都打成同一个时刻。实测约**一半的对象带 `2`**,最大偏差 21 秒。

| `Objs` 字段 | 含义 | 与 `PVs` 同名字段的关系 |
|---|---|---|
| `N` | 变量名(≤128 字符) | — |
| `1` | 实时值(`PNs.1`="V") | **绝对值**,等于 `PVs.1` 时该字段省略 |
| `2` | 时间戳(`PNs.2`="T") | **偏移量,单位毫秒**,为 0 时该字段省略 |
| `3` | 质量戳(`PNs.3`="Q") | 绝对值(192=GOOD,0=BAD),等于 `PVs.3` 时省略 |

字段编号就是 `PNs` 的下标。省略的字段一律用 `@#value.N|PVs.N` 回落到基准值。

## 内置的两条规则

**方法二 — kxMQTT 转发**(报文 `{"PNs":...,"PVs":...,"Objs":[...]}`)

```json
{ "match": "@PNs",
  "each": "@Objs",
  "template": { "DataName": "@#value.N",
                "DataValue": "@#value.1|PVs.1",
                "DataTime": "@#ts",
                "DataQuality": "@#value.3|PVs.3" } }
```
→ `{"DataName":"pub10","DataValue":1616,"DataTime":"2026-10-09 17:16:47.569","DataQuality":192}`

**方法一 — MQTT_Request 驱动 PUB0.0 触发**(报文 `{"/tag1":0, "/tag2":0, ...}`)

```json
{ "match": "@/tag1",
  "each": "@.",
  "template": { "变量名": "@#key", "值": "@#value", "时间戳": "@#now" } }
```
→ 20 个 tag 各发一条 `{"变量名": "/tag20", "值": 20, "时间戳": "..."}`

改格式 = 改 `template`/`each`/`match`,存盘重启进程即可,不用碰代码。

## 兼容性

MQTT 是开放标准,不是 EMQX 专用。

| 项目 | 支持 | 说明 |
|---|---|---|
| 协议 | MQTT 3.1.1 | 所有 broker 兼容 |
| 传输 | 纯 TCP | WebSocket 需改代码 |
| 加密 | 无 TLS | 8883/TLS、云平台证书认证需扩展 |
| 认证 | 用户名/密码 | 云平台 Token/证书需扩展 |
| QoS | 0 | QoS1/2 需补重传 |
| 平台 | 任意 Python3 | Windows/Linux/Mac 通用 |

内网自建 broker(EMQX/Mosquitto/HiveMQ/VerneMQ/NanoMQ)改 `broker.host/port` 即可。

## 为什么不用改 DLL

一度用二进制补丁改过 `kxMQTT.dll`(反汇编定位字符串 + 指针重定向 + 代码洞跳板),
技术上可行,但**不划算**:

- 只能管 kxMQTT 这条路,方法一(PUB0.0)管不到
- 每改一次格式都要重新逆向
- 厂商升级直接覆盖,补丁全废
- 需要处理槽位宽度、寄存器复用等细节

已**回滚为原厂**(MD5 `F5CD50568C6A9E044E1C5253BD3A4C3E`)。
`BIN\kxMQTT.dll.orig` 与桌面 `kxMQTT.dll` 仍是原件备份。

逆向结论留在 `patch_step1.py` / `classify_xrefs.py` 等脚本里,以备将来需要。

## 装成 Windows 服务

任务计划程序开机自启即可:

```
程序:  C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe
参数:  -I C:\Users\Administrator\kio-mqtt-norm\kio_reshaper.py
工作目录: C:\Users\Administrator\kio-mqtt-norm
触发器: 系统启动时
```

或用 [NSSM](https://nssm.cc) / `sc create` 注册为服务。

## 历史数据与存储

审计追踪里的两类记录**分开存**，各走各的策略：

| | 报文记录 | 动作记录 |
|---|---|---|
| 内容 | 每条进出的 MQTT 报文 | 配置的**增删改** + 连接运行事件 |
| 存储 | **分段分时**，多个小库 | **单文件**，不分段 |
| 位置 | `坤\messages\` | `坤\actions.db` |

### 报文记录：分段

```
坤\messages\
├── 2026-10-10-01.db   ← 归档段
├── 2026-10-13-01.db   ← 当前段（正在写入）
└── 2026-10-13-02.db   ← 同一天写满后接着开的
```

文件名固定 **`yyyy-mm-dd-序号.db`**。开新库的两个触发条件，谁先到谁触发：

| 条件 | 默认 | 说明 |
|---|---|---|
| **存储上限** | 500 MB | 单个库文件写到上限就开新库 |
| **分段周期** | 1 天 | 每隔 N 天开一个新库（1 = 一天一个） |

**为什么分段**：单文件方案里 `DELETE` 不会缩小 SQLite 文件（空页留作复用，`VACUUM` 还得硬压）。分段之后删旧数据 = 删掉整个 `.db` 文件，磁盘空间立刻回来。

**旧数据不自动删**。库多了去「存储管理」页手动删：清空全部 / 删最旧 N 个库 / 清某个时间段 / 删单个库。

**连接与断开**：每个库可单独连接或断开。断开 = 查询不再命中它，并释放文件句柄（方便搬走或备份）。归档段默认保持连接，所以历史一直查得到。正在写入的那个库始终连接，不能断开也不能删除。

### 动作记录：增删改留痕

配置的每一次改动都单独记一条，带**类别 / 动作 / 对象 / 改了什么**：

| 类别 | 记什么 |
|---|---|
| `broker` | 连接预设的新增 / 修改 / 删除 |
| `方案` | 转发方案的新增 / 修改 / 删除 |
| `规则配置` | 转换规则的新增 / 修改 / 删除 |
| `数据库` | 段的连接/断开/删除、手动清除、存储设置 |
| `连接` | 断线重连、启动、失败等运行事件 |
| `系统` | 迁移等杂项 |

改动是**在服务端按名字对出来的**，不是靠界面按钮上报——所以不管从哪儿改，都会留痕。改动详情只写**字段名**（如「改动项：port」），密码改动也不带出值。

界面回传的 `has_password` 是展示字段，**不会**被当成改动，避免一堆误报。

### 目录

```
坤\
├── messages\        ← 报文，分段 yyyy-mm-dd-NN.db
└── actions.db       ← 动作，单文件
```

**兼容**：旧的单文件 `kio_history.db`、旧的 `data\` 目录、以及早先段里的 `events` 表，启动时会自动搬过来（报文进 `坤\messages\`，事件进 `坤\actions.db`），一条不丢。

## 测试

测试脚本都在 `乾\3造势\`，会自己算出 `乾\2运势` 的位置，直接跑即可：

```
cd 乾\3造势
python test_actions.py     # 动作记录：增删改留痕、类别筛选
python test_storage.py     # 分段：轮转、命名、连接/断开、手动清除、迁移
python test_size.py        # 容量上限：写满切段、单库受限、一条不丢
python test_direct.py      # 直发/转发两类报文按规则转换
python test_ts.py          # @#ts 时间偏移
python test_ping.py        # 测试通路（需 WebUI 在跑）
python test_persist.py     # 保存持久化（需 WebUI 在跑）
python test_pwd.py         # 密码不外泄（需 WebUI 在跑）
node check_save.js ../2运势/webui.html   # 规则编辑器：保存/展开/复制
node checkjs.js   ../2运势/webui.html x.js
node smoke.js     ../2运势/webui.html
```

`test_reshaper.py <输入主题> <载荷文件> <输出主题>` 走真 broker 验一条报文。
`payload*.json` / `out*.json` 是两种格式的样例载荷与输出。
载荷必须走文件传参:PowerShell 5.1 向原生程序传参会吞掉 JSON 引号。
