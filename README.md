# PcController — 跨平台局域网远程控制

一个轻量的局域网远程桌面工具,**任意平台控制任意平台**:Windows↔Windows、Mac↔Mac、Mac→Windows、Windows→Mac 都可以(Linux 也支持)。

两个角色,和操作系统无关:

| 角色 | 脚本 | 跑在哪台机器 | 作用 |
|------|------|-------------|------|
| **被控端 / Agent** | `agent.py` | 你**想控制**的那台电脑 | 采集屏幕并推流;接收并注入鼠标/键盘 |
| **控制端 / Controller** | `controller.py` | 你**用来操作**的那台电脑 | 显示对方画面;采集本机鼠标/键盘并转发 |

```
控制端 controller.py  ──TCP(局域网)──▶  被控端 agent.py
  显示画面 / 采集输入                        采集屏幕 / 注入鼠标键盘
```

纯 Python,依赖 `mss`(截屏)、`pynput`(输入注入)、`Pillow`(图像编码)、`tkinter`(界面)。这几个库在 Windows / macOS / Linux 上都能用,所以同一份代码三端通用。归一化坐标传输,两台机器分辨率不同也没关系。

---

## 支持矩阵

| 控制端 ＼ 被控端 | Windows | macOS | Linux |
|---|---|---|---|
| **Windows** | ✅ | ✅ | ✅ |
| **macOS** | ✅ | ✅ | ✅ |
| **Linux** | ✅ | ✅ | ✅ |

---

## 一、安装依赖(在**每台**参与的电脑上各做一次)

### Windows
本项目目录下已建好虚拟环境 `.venv`(含全部依赖),Windows 直接用即可。
在别的 Windows 机器上则:
```powershell
python -m pip install -r requirements.txt
```
> 用 [python.org](https://www.python.org/downloads/windows/) 的安装包,自带 `tkinter`。

### macOS
```bash
python3 -m pip install -r requirements.txt
# 若用 Homebrew 的 python,tkinter 需单独装:brew install python-tk
```
**做被控端时**必须在 `系统设置 → 隐私与安全性` 里给运行的**终端**授予两项权限(授权后要**完全退出终端再重开**):
- **屏幕录制 (Screen Recording)** —— 否则截屏是黑屏。
- **辅助功能 (Accessibility)** —— 否则无法控制鼠标键盘。

### Linux(可选)
```bash
sudo apt install python3-tk        # tkinter
python3 -m pip install -r requirements.txt
```
> 截屏/注入在 **X11** 下工作良好;**Wayland** 对截屏和输入注入有限制,建议改用 X11 会话。

---

## 二、运行(两步)

### 1) 在**被控端**(要被控制的电脑)启动 agent
```bash
# macOS / Linux
python3 agent.py --password 你的密码
# Windows
python agent.py --password 你的密码
```
看到 `listening on 0.0.0.0:50505` 即就绪。先查好这台机器的局域网 IP:
- macOS: `ipconfig getifaddr en0`
- Windows: `ipconfig`(找 IPv4 地址)
- Linux: `hostname -I`

### 2) 在**控制端**(你操作的电脑)启动 controller
```bash
# Windows(用本项目自带 venv)
C:\test\PcController\.venv\Scripts\python.exe controller.py --host 192.168.1.50 --password 你的密码
# macOS / Linux
python3 controller.py --host 192.168.1.50 --password 你的密码
```
`--host` 填被控端的局域网 IP,`--password` 两端一致。连上后弹出窗口实时显示对方桌面:
- **鼠标**:窗口内移动/点击/滚轮 = 操作对方。
- **键盘**:窗口获得焦点后直接输入。修饰键**按物理键原样转发**(见下方"跨平台按键说明")。
- **退出**:关闭窗口,或按 `Ctrl+Alt+Q`。断开时被控端会自动释放所有按下的键,不会卡键。

---

## 三、跨平台按键说明(重要)

按键是**按物理键原样转发**的,不做"智能改键":

- **Windows 控制 Mac**:Windows 的 `Win` 键 = Mac 的 `Command`。所以复制粘贴按 `Win+C` / `Win+V`。
- **Mac 控制 Windows**:Mac 的 `Command` 键 = Windows 的 `Win` 键(不是 Ctrl)。要在 Windows 上复制粘贴,请按**物理 `Control` 键**:`Ctrl+C` / `Ctrl+V`。
- **控制端是 macOS 时的限制**:少数 macOS 系统级快捷键(如 `Cmd+Q`、`Cmd+Tab`、`Cmd+Space`)会被 macOS/Tk 本地拦截,可能不会转发给对方。普通输入和绝大多数组合键不受影响。

---

## 四、常用参数(被控端 agent.py)

| 参数 | 说明 | 默认 |
|------|------|------|
| `--password` | 连接密码(两端必须一致) | `changeme` |
| `--port` | 监听端口 | `50505` |
| `--fps` | 每秒帧数 | `15` |
| `--quality` | JPEG 画质 1–95(越低越省带宽) | `60` |
| `--scale` | 缩放系数,如 `0.75` 降分辨率省带宽 | `1.0` |
| `--monitor` | 显示器序号(1=主屏,0=全部拼接) | `1` |

**卡顿**:`--scale 0.7 --quality 45 --fps 12`　**要清晰**:`--scale 1.0 --quality 75`

---

## 五、自测(无需第二台电脑)

在任意一端本机跑回环自测,一条命令验证「截屏 → 推流 → 注入」整条链路(相当于本机控制本机):
```bash
python smoke_test.py         # Windows 上用 .venv\Scripts\python.exe
```
会打印实测帧率,并把鼠标移到屏幕中心再移回原位(不输入任何字符)。

运行全部单元/集成测试(29 项):
```bash
python -m unittest discover -s tests -v
```

---

## 六、排错

| 现象 | 原因 / 解决 |
|------|------|
| `connection refused` | 被控端没启动 / IP、端口不对 / 防火墙拦截 |
| 画面全黑(被控端是 Mac) | 未授予**屏幕录制**,或授权后未重开终端 |
| 鼠标键盘无反应(被控端是 Mac) | 未授予**辅助功能**,或授权后未重开终端 |
| 连不上但 ping 通 | 被控端系统防火墙放行该端口/程序 |
| 画面卡顿 | 降低 `--fps` / `--quality` / `--scale` |
| 认证失败 | 两端 `--password` 不一致 |
| Mac 上 `No module named tkinter` | `brew install python-tk`(或用 python.org 的安装包) |

---

## 七、安全说明

- 明文 TCP 传输,仅供**可信局域网**使用,勿暴露公网。
- 务必修改默认密码 `changeme`。一次只接受一个控制端连接。

## 八、项目结构

```
remote_control/
  protocol.py       # TCP 定长头消息 framing
  config.py         # 默认参数 + ServerConfig
  input_handler.py  # 输入事件 -> pynput 注入(含断开释放,防卡键)
  server.py         # 被控端:截屏 + 注入(跨平台)
  client.py         # 控制端:Tkinter GUI(跨平台滚轮/按键)
  clientutil.py     # 纯函数:坐标映射 / 滚轮 / 按键翻译
agent.py            # 被控端入口(任意 OS)
controller.py       # 控制端入口(任意 OS)
smoke_test.py       # 回环自测
tests/              # 单元 + 集成测试(29 项)
```
