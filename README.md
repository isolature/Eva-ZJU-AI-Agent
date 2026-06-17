# Eva —— 钉钉群 AI Agent

基于手写 ReAct 主循环的 AI Agent，部署在阿里云服务器上，通过钉钉 Stream 模式接入钉钉群聊。在群里 @Eva 即可对话，她能查天气、联网搜索、查教务通知、管理日程提醒、记住你的课表和个人偏好，还会每天早上主动推送晨报。

核心特点：**没有使用任何 Agent 框架**——整个 Agent 就是一个手写的 while 循环（`run_agent.py`），调用 DeepSeek 原生 Function Calling 接口，让模型自主决定调哪些工具、传什么参数，程序负责执行并把结果回灌，直到模型给出最终回答。

## 功能一览

| 功能 | 说明 |
|---|---|
| 天气查询 | 和风天气 API，实时天气 |
| 联网搜索 | Tavily 搜索 API |
| 教务通知 | 抓取浙大本科教务网，支持 WebVPN 校外访问 |
| 日程提醒 | 自然语言创建提醒，后台调度器到点私聊推送 |
| 课程查询 | JSON 导入课表，按星期查询 |
| 长期记忆 | 记住用户画像（专业/爱好/习惯），跨会话保持 |
| 公众号通知 | 通过 we-mp-rss 抓取微信公众号文章，重要通知主动推送 |
| 每日晨报 | 每天定时汇总课表+提醒+天气+通知，主动推送 |

## 项目结构

```
├── main.py                 # 入口：装配所有零件并启动钉钉 Stream
├── run_agent.py            # 手写 Agent 主循环 + TOOLS + build_dispatch
├── communication/          # 通信层
│   ├── bot_handler.py      # 钉钉收发、多轮短期记忆、上下文注入
│   ├── notifier.py         # 主动推送（提醒/简报/公众号共用）
│   └── campus_http.py      # 浙大 WebVPN：校外访问校内教务网
├── tools/                  # 工具函数（每个工具 = 返回字符串的函数）
│   ├── weather.py          # 和风天气
│   ├── web_search.py       # Tavily 联网搜索
│   ├── zju_notices.py      # 教务网通知
│   ├── reminder.py         # 日程提醒 CRUD
│   ├── course.py           # 课表查询
│   ├── memory.py           # 长期记忆 CRUD
│   └── wechat.py           # 公众号文章查询
├── services/               # 后台服务（每个一个子包）
│   ├── reminder/           # 存储 + 服务 + 调度器
│   ├── briefing/           # 每日简报
│   ├── memory/             # 用户画像 + 课表（SQLite）
│   └── wechat_rss/         # 消费 we-mp-rss：抓取/存储/摘要/调度
├── utils/
│   └── time_parser.py      # 北京时间解析/格式化
├── config/
│   └── courses.json        # 课表数据
├── data/
│   └── agent.db            # SQLite 数据库（运行时自动创建）
├── .env.example            # 环境变量模板
└── requirements.txt        # Python 依赖
```

---

## 复刻指南

按以下步骤，你可以从零部署一个属于自己的 Eva。

### 第一步：获取云服务器

Eva 需要 24 小时在线，所以需要一台云服务器。推荐阿里云 ECS。

**学生免费领取：** 阿里云为在校学生提供免费云服务器，访问 [阿里云学生优惠页面](https://university.aliyun.com/) 完成学生认证后即可领取。

领取后在阿里云控制台创建一台 ECS 实例：

- **操作系统**：Ubuntu 22.04 LTS
- **配置**：学生机的 2 核 2G 足够跑 Eva（Eva 本身不跑模型，只做 API 调用）
- **安全组**：默认即可，Eva 使用钉钉 Stream 模式（长连接），不需要开放公网端口、不需要域名和备案

创建完成后，记下服务器的公网 IP，用 SSH 连接：

```bash
ssh root@你的服务器公网IP
```

### 第二步：服务器环境配置

```bash
# 更新系统
apt update && apt upgrade -y

# 安装 Python 3.10+（Ubuntu 22.04 自带）
python3 --version

# 安装 pip 和 venv
apt install -y python3-pip python3-venv

# 创建项目目录
mkdir -p /opt/eva && cd /opt/eva

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate
```

克隆本仓库并安装依赖：

```bash
git clone https://github.com/isolature/Eva-ZJU-AI-Agent.git /opt/eva/app
cd /opt/eva/app
pip install -r requirements.txt
```

### 第三步：申请各项 API Key

Eva 需要以下外部服务，都有免费额度：

#### 3.1 钉钉应用（必填）

1. 登录 [钉钉开放平台](https://open-dev.dingtalk.com/)，创建一个企业内部应用
2. 在「凭证与基础信息」页拿到 `AppKey` 和 `AppSecret`
3. 在「消息推送」里启用 **Stream 模式**（不是 Webhook）
4. 在「机器人」里开启群聊机器人能力
5. 记下应用的 `AgentId`（用于主动推送消息）
6. 把机器人添加到你的钉钉群里

> **获取你的 userid**：在钉钉管理后台（oa.dingtalk.com）→ 通讯录 → 点击你自己 → URL 里的 userid 参数，或通过 API 查询。

#### 3.2 DeepSeek（必填）

1. 注册 [DeepSeek 开放平台](https://platform.deepseek.com/)
2. 在 API Keys 页面创建一个 Key
3. 账户里充几块钱即可用很久（日常对话每天消耗不到 1 毛）

#### 3.3 和风天气（天气功能）

1. 注册 [和风天气开发平台](https://dev.qweather.com/)，完成实名认证
2. 控制台新建项目，拿到 API Key
3. 在设置里查看你的专属 API Host（形如 `xxx.qweatherapi.com`）

#### 3.4 Tavily（联网搜索功能）

1. 注册 [Tavily](https://app.tavily.com/)
2. 拿到 API Key（`tvly-` 开头），有 1000 次免费额度

#### 3.5 浙大 WebVPN（教务通知功能，非浙大学生可跳过）

如果你的服务器在校外，需要通过 WebVPN 访问教务网 zdbk.zju.edu.cn。填入你的浙大统一身份认证账号密码即可，程序会自动走 WebVPN 隧道。

### 第四步：配置环境变量

```bash
cd /opt/eva/app
cp .env.example .env
vim .env    # 把各项占位符替换为你的真实值
```

`.env` 文件中的关键配置：

```env
# 钉钉（必填）
DINGTALK_APP_KEY=你的AppKey
DINGTALK_APP_SECRET=你的AppSecret
DINGTALK_AGENT_ID=你的AgentId
DINGTALK_OWNER_USERID=你的钉钉userid

# DeepSeek（必填）
DEEPSEEK_API_KEY=你的Key

# 和风天气
QWEATHER_API_KEY=你的Key
QWEATHER_API_HOST=你的专属Host

# Tavily 联网搜索
TAVILY_API_KEY=你的Key

# 浙大 WebVPN（非浙大学生留空即可）
ZJU_WEBVPN_MODE=auto
ZJU_WEBVPN_USERNAME=
ZJU_WEBVPN_PASSWORD=

# 每日晨报
DAILY_BRIEFING_ENABLED=true
DAILY_BRIEFING_TIME=07:30
DAILY_BRIEFING_CITY=杭州
```

### 第五步：配置课表（可选）

编辑 `config/courses.json`，按格式填入你的课表：

```json
[
  {
    "name": "课程名称",
    "weekday": 1,
    "start_time": "08:00",
    "end_time": "09:35",
    "location": "教室",
    "teacher": "老师",
    "weeks": [1, 2, 3, 4, 5, 6, 7, 8]
  }
]
```

其中 `weekday` 为 1-7（周一到周日），`weeks` 为上课的周次列表。

### 第六步：启动运行

```bash
# 先手动测试
cd /opt/eva/app
source /opt/eva/venv/bin/activate
python main.py
```

看到 `已连接钉钉，等待群里 @机器人……` 就说明启动成功了。在钉钉群里 @机器人 试试。

#### 用 systemd 保持后台运行

创建服务文件让 Eva 开机自启、崩溃自动重启：

```bash
cat > /etc/systemd/system/eva.service << 'EOF'
[Unit]
Description=Eva DingTalk AI Agent
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/eva/app
ExecStart=/opt/eva/venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 启用并启动
systemctl daemon-reload
systemctl enable eva
systemctl start eva

# 查看运行状态
systemctl status eva

# 查看日志
journalctl -u eva -f
```

### 第七步：公众号通知功能（可选）

如果你想让 Eva 也能推送微信公众号的文章通知，需要额外部署 [we-mp-rss](https://github.com/cooderl/we-mp-rss) 项目。

可以部署在同一台服务器上，也可以用另一台。推荐用 Docker：

```bash
# 拉取镜像并启动（默认监听 8001 端口）
docker run -d --name we-mp-rss \
  -p 8001:8001 \
  -v /opt/we-mp-rss/data:/app/data \
  docker.lms.run/ranfos/we-mp-rss:latest \
  "/app/start.sh"
```

启动后访问 `http://你的服务器IP:8001` 进入 we-mp-rss 后台，添加你想订阅的公众号。

然后在 Eva 的 `.env` 里配置 RSS 地址：

```env
# 同机部署
WECHAT_RSS_URL=http://127.0.0.1:8001/feed/all.json

# 跨服务器部署
WECHAT_RSS_URL=http://另一台服务器IP:8001/feed/all.json
```

重启 Eva 即可生效。

---

## 技术原理

如果你对 Eva 的内部实现感兴趣，这里简要说明核心设计。

**Agent 主循环**（`run_agent.py`）：一个 `for` 循环，每轮把对话历史 + 工具清单发给 DeepSeek，如果模型返回 `tool_calls` 就执行对应工具并把结果回灌，否则返回最终回答。最多循环 6 步，防止无限调用。

**工具系统**：每个工具有两副面孔——给模型看的 JSON Schema（`TOOLS` 列表）和给程序执行的函数（`build_dispatch()` 字典）。新增工具只需三步：写函数 → 加 Schema → 登记一行 dispatch。

**三层架构**：通信层（钉钉收发）、主体层（Agent 决策循环）、服务层（提醒/记忆/简报等后台功能）彼此解耦。


## License

MIT
