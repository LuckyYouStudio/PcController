# 部署中转/信令服务器到阿里云香港

服务器程序 = `relay_server.py`(信令:STUN + 端点交换 + 中转兜底)。
**纯 Python 3 标准库,不用 `pip install` 任何东西。** CPU/内存几乎不吃,主要看带宽:
P2P 打通后服务器基本不耗流量;只有严格 NAT 回退中转时才走服务器带宽。

---

## 1. 买 ECS(阿里云香港)

- **地域**:中国香港
- **实例规格**:最低配即可(1 vCPU / 1–2 GB)
- **镜像**:Ubuntu 22.04 64 位
- **公网**:分配公网 IP;带宽选「按使用流量」(便宜)或一条小的固定带宽
- 设置 root 密码(或绑定 SSH 密钥)
- 香港地域**不需要 ICP 备案**

## 2. 配置安全组(最关键的一步)

进入 ECS 的**安全组 → 入方向 → 手动添加**,加**两条**规则:

| 协议类型 | 端口范围 | 授权对象(源) |
|---------|---------|---------------|
| 自定义 TCP | 50510/50510 | 0.0.0.0/0 |
| 自定义 UDP | 50510/50510 | 0.0.0.0/0 |

> ⚠️ **TCP 和 UDP 都要放行!** 信令控制走 TCP,STUN/打洞走 UDP。
> 少放 UDP 的话打洞永远失败,只能退回中转(还能用,但失去了省带宽的意义)。

## 3. 登录并一键安装

SSH 登录(记下公网 IP):

```bash
ssh root@<你的公网IP>
```

一键安装(自动装 python3/git、拉代码、装 systemd 服务并开机自启):

```bash
curl -fsSL https://raw.githubusercontent.com/LuckyYouStudio/PcController/main/deploy/install.sh | bash -s -- 50510
```

或手动:

```bash
apt update && apt install -y git python3
git clone --depth 1 https://github.com/LuckyYouStudio/PcController.git /opt/PcController
bash /opt/PcController/deploy/install.sh 50510
```

## 4. 确认在运行

```bash
systemctl status pccontroller-relay
journalctl -u pccontroller-relay -n 20
```

看到 `[signaling] TCP+UDP on 0.0.0.0:50510` 就成了。

## 5. 客户端配置

两台 PcController 都在**互联网**标签把「中转服务器」填成:

```
<你的公网IP>:50510
```

- 被控端:显示「您的 ID + 密码」分享出去
- 控制端:填「对方 ID + 密码」→ 连接

## 6. 连通性自测

- **TCP**(在客户端电脑上):
  - Windows:`Test-NetConnection <公网IP> -Port 50510`(`TcpTestSucceeded : True` = TCP 通)
  - Mac/Linux:`nc -vz <公网IP> 50510`
- **UDP** 不好单独测,直接用 App 拿两台不同网络的机器连一次,看被控端窗口/日志:
  - `远程控制端已接入(方式:p2p)` = 打洞成功,视频直连、不耗服务器带宽 ✅
  - `方式:relay` = 打洞失败,走了中转兜底(检查 UDP 安全组是否放行)

---

## 运维

```bash
# 更新到最新代码
cd /opt/PcController && git pull && systemctl restart pccontroller-relay
# 实时日志
journalctl -u pccontroller-relay -f
# 停止 / 启动
systemctl stop pccontroller-relay
systemctl start pccontroller-relay
```

## 说明与注意

- **安全**:信令与中转目前是明文;中转兜底时服务器能看到字节流。属于「可信自建服务器」范畴,够用;要更强安全以后可加 TLS。
- **带宽成本**:P2P 命中时服务器几乎零流量;回退中转时一路会话约 6–10 Mbps。跨境(大陆↔香港)网络偶有波动,延迟高于同区。
- **端口**:如需换端口,`install.sh <PORT>` 传别的端口,并在安全组同步放行该端口的 TCP+UDP。
