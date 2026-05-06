# ZeroClaw 浏览器自动化排错与配置笔记

> 记录日期：2026-05-06
> 目标主机：`dqj@192.168.4.39`（Ubuntu 24.04，Linux x86_64）
> 本地工作目录：`/Users/qingjie.du/HDD/d-zeroclaw`（Mac，git master）
> 本次改动版本：ZeroClaw 0.7.4 + master 上 `native_cdp_address` 特性（commit `380e8137`）

本文档把这次让 ZeroClaw agent 能够操控**已有的** Chrome（监听 9222 端口、保留登录 session 的那个）的完整诊断和修复流程记下来，避免下次重复走弯路。

---

## 1. 背景与症状

Agent 在执行带 GUI 操作的浏览器任务（点击、填表、截图）时报：

> 当前运行环境的浏览器后端配置为 `rust_native`，但它没有启用 `browser-native` 构建特性。
> 这意味着我无法通过 9222 端口连接并操控你的本地 Chrome 浏览器进行点击、填表等 GUI 操作。
> 检查 ZeroClaw 环境的 `browser.backend` 配置是否支持 `agent-browser` 或 `computer_use`。

**判断**：Agent 的诊断（"二进制没编 `browser-native`"）是对的，但它给出的"换 backend"建议是误导——换 backend 不能解决"接管已存在的 9222 Chrome"这个目标，那条路径只属于 `rust_native` + `native_cdp_address`。

---

## 2. ZeroClaw 浏览器后端的全貌

`browser.backend` 共四个值，依赖完全不同：

| backend | 依赖 | 适用场景 |
|---|---|---|
| `agent_browser` | npm 包 `agent-browser`（外部 CLI） | 默认值，不需要 Rust feature；它走自己的会话模型，不能挂到外部 Chrome |
| `rust_native` | **构建时** `--features browser-native` + 运行时 ChromeDriver | 直接通过 WebDriver/CDP 操控 Chrome |
| `computer_use` | 外部 sidecar 服务 | 通过视觉模型操控 |
| `auto` | 上述任一可用 | 自动按 rust-native → agent-browser → computer-use 顺序回退 |

### `rust_native` + `native_cdp_address`：本次走的路径

近期 master 增加了 [`native_cdp_address`](crates/zeroclaw-config/src/schema.rs#L2743-L2750) 字段，含义是：

> ChromeDriver 仍要在跑（`native_webdriver_url` 指向它），但它会**挂到一个已经存在的 Chrome 实例**，而不是再新拉一个。`native_headless` 在设置了这个字段后会被忽略。

正是它让"保留 Chrome 登录态 / cookies"的玩法成为可能。

---

## 3. 诊断关键步骤（带命令）

### 3.1 找到运行中的 daemon 二进制

```bash
ssh dqj@192.168.4.39 'ps -ef | grep "zeroclaw daemon" | grep -v grep'
# 输出形如：
# dqj   1856398   1100  0 12:12 ? /home/dqj/.cargo/bin/zeroclaw daemon
```

**注意**：服务器上同时存在 `~/zeroclaw-build/target/release/zeroclaw` 和 `~/zeroclaw-build-0.7.4/target/release/zeroclaw`，但**真正运行的不是它们**——是 `~/.cargo/bin/zeroclaw`（手工 `cp` 过去的，不是 `cargo install` 装的——`~/.cargo/.crates.toml` 没有 zeroclaw 条目）。

### 3.2 验证二进制是否含 `browser-native`

```bash
strings ~/.cargo/bin/zeroclaw | grep -c -i fantoccini
```
- `0` → 没编入（`browser-native` 关闭）
- `>0` → 编入了（典型值 5 左右）

`fantoccini` 是 rust-native backend 唯一的 WebDriver client crate，是最可靠的指纹。`browser-native` feature 在 `crates/zeroclaw-tools/Cargo.toml` 里被定义为 `["dep:fantoccini"]`。

附加佐证：含 `browser-native` 的二进制不会有这串字符串：
```
"Rust-native browser backend is not compiled. Rebuild with --features browser-native"
```
（这只出现在 `#[cfg(not(feature = "browser-native"))]` 的分支里。）

### 3.3 检查 daemon 进程怎么管的

```bash
ssh dqj@192.168.4.39 'systemctl --user list-units --type=service | grep -i zeroclaw'
# zeroclaw.service  loaded active running ZeroClaw daemon
```

是 systemd user service。重启用：
```bash
systemctl --user stop zeroclaw
systemctl --user start zeroclaw
systemctl --user status zeroclaw --no-pager | head -20
```

### 3.4 检查 9222 / 9515 是否在跑

```bash
ssh dqj@192.168.4.39 'ss -tlnp | grep -E "9222|9515"'
# 9515 → chromedriver
# 9222 → /opt/google/chrome/chrome --remote-debugging-port=9222 \
#                                  --user-data-dir=/tmp/chrome-debug \
#                                  --no-first-run --no-default-browser-check

# CDP 健康自检
ssh dqj@192.168.4.39 'curl -s --max-time 3 http://127.0.0.1:9222/json/version'
# 期望：返回带 Browser/Protocol-Version/webSocketDebuggerUrl 的 JSON
```

### 3.5 看 `[browser]` 配置

```bash
ssh dqj@192.168.4.39 'awk "/^\[browser\]/{flag=1;next} /^\[/{flag=0} flag" ~/.zeroclaw/config.toml'
```
本次修复前内容：
```toml
enabled = true
allowed_domains = ["*"]
backend = "rust_native"
native_headless = false
native_webdriver_url = "http://127.0.0.1:9515"
# 缺 native_cdp_address
```

### 3.6 服务器源码版本是否含 `native_cdp_address`

```bash
ssh dqj@192.168.4.39 'grep -n native_cdp_address ~/zeroclaw-build-0.7.4/crates/zeroclaw-config/src/schema.rs'
# 没匹配 → 老源码，必须更新
```

---

## 4. 修复步骤（完整记录）

### 4.1 服务器端备份

```bash
ssh dqj@192.168.4.39 '
  cp ~/.cargo/bin/zeroclaw ~/.cargo/bin/zeroclaw.bak.before-cdp
  cp ~/.zeroclaw/config.toml ~/.zeroclaw/config.toml.bak.before-cdp-$(date +%Y%m%d-%H%M%S)
  rm -rf ~/zeroclaw-master-cdp && mkdir -p ~/zeroclaw-master-cdp
'
```

### 4.2 从 Mac rsync 源码到服务器

```bash
# 在 /Users/qingjie.du/HDD/d-zeroclaw（macOS bundled rsync 不支持 --info）
rsync -az --stats \
  --exclude='target/' --exclude='.git/' --exclude='node_modules/' \
  --exclude='dist/'   --exclude='.DS_Store' --exclude='*.log' \
  --exclude='.next/'  --exclude='build/' \
  ./ dqj@192.168.4.39:zeroclaw-master-cdp/
# 源码 ~37MB，1132 文件
```

> macOS 自带 rsync 是老版本，`--info=stats1` 不识别，用 `--stats` 替代。

### 4.3 在服务器上编译（**注意：下次用交叉编译**）

```bash
ssh dqj@192.168.4.39 '
  export PATH=$HOME/.cargo/bin:$PATH
  cd ~/zeroclaw-master-cdp
  nohup bash -c "PATH=\$HOME/.cargo/bin:\$PATH cargo build --release --features browser-native --bin zeroclaw" \
    > /tmp/zeroclaw-build.log 2>&1 < /dev/null & disown
'
```

实际耗时：**12 分 01 秒**（4 核 box，build 时 load avg 飙到 5.0）。

> **重要**：下次重编强烈建议改用 Mac 上的交叉编译——见第 7 节。

非交互 SSH 拿不到 `~/.cargo/bin` 的 PATH，所以要显式 export。`Cargo.toml` 默认 features 已经包含 `browser-native`（第 346 行），但显式加 `--features browser-native` 更稳。

### 4.4 验证新二进制

```bash
ssh dqj@192.168.4.39 '
  ls -la ~/zeroclaw-master-cdp/target/release/zeroclaw
  strings ~/zeroclaw-master-cdp/target/release/zeroclaw | grep -c -i fantoccini   # 期望 >0
  ~/zeroclaw-master-cdp/target/release/zeroclaw --version
  ~/zeroclaw-master-cdp/target/release/zeroclaw --help | head -10
'
```

### 4.5 编辑 config 加入 `native_cdp_address`

服务器端 `python3` 直接定位+插入（避免 sed 的换行/转义陷阱）：

```bash
ssh dqj@192.168.4.39 'python3 - <<"PY"
import pathlib
p = pathlib.Path("/home/dqj/.zeroclaw/config.toml")
t = p.read_text()
needle = "native_webdriver_url = \"http://127.0.0.1:9515\""
ins    = "\nnative_cdp_address = \"127.0.0.1:9222\""
assert needle in t, "needle not found"
assert "native_cdp_address" not in t, "already present"
i = t.index(needle) + len(needle)
p.write_text(t[:i] + ins + t[i:])
print("inserted")
PY'
```

修复后 `[browser]` 段：
```toml
[browser]
enabled = true
allowed_domains = ["*"]
backend = "rust_native"
native_headless = false
native_webdriver_url = "http://127.0.0.1:9515"
native_cdp_address = "127.0.0.1:9222"
```

### 4.6 停 daemon → 换二进制 → 启 daemon

```bash
ssh dqj@192.168.4.39 '
  systemctl --user stop zeroclaw
  cp ~/zeroclaw-master-cdp/target/release/zeroclaw ~/.cargo/bin/zeroclaw
  systemctl --user start zeroclaw
  sleep 3
  systemctl --user status zeroclaw --no-pager | head -20
'
```

### 4.7 健康检查

```bash
ssh dqj@192.168.4.39 'timeout 30 ~/.cargo/bin/zeroclaw doctor'
# 期望 Summary: 30 ok, 0 warnings, 0 errors
# 重点看：[daemon] heartbeat fresh, channel:* fresh
```

实际验证（让 agent 真正点一下 9222 Chrome）的最稳方式是通过 Telegram/Discord/CLI 发一条消息让 agent 跑一个 read-only 的 browser action（如 `open <已登录 URL>` + `get_title` 或 `screenshot`）。

---

## 5. 回滚预案

如果新二进制有问题或要紧急退回旧版：

```bash
ssh dqj@192.168.4.39 '
  systemctl --user stop zeroclaw
  cp ~/.cargo/bin/zeroclaw.bak.before-cdp ~/.cargo/bin/zeroclaw
  cp ~/.zeroclaw/config.toml.bak.before-cdp-20260506-194849 ~/.zeroclaw/config.toml
  systemctl --user start zeroclaw
  systemctl --user status zeroclaw --no-pager | head -10
'
```

---

## 6. Chrome 起 9222 的标准姿势

ZeroClaw `rust_native + native_cdp_address` 要求**先**有这样的 Chrome 进程：

```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-debug \
  --no-first-run \
  --no-default-browser-check \
  >/tmp/chrome-9222.log 2>&1 &
```

并且 ChromeDriver 监听 9515：
```bash
chromedriver --port=9515 >/tmp/chromedriver.log 2>&1 &
```

`--user-data-dir` 决定了 cookies / 登录态存哪里。把它指向同一个目录就能跨 session 复用登录状态（reddit、Twitter、内部系统等）。

> 当前服务器上的 Chrome 是用 `--user-data-dir=/tmp/chrome-debug` 起的，重启服务器后 `/tmp` 会清空——若要保留登录态，把 user-data-dir 换成持久路径，比如 `~/.chrome-zeroclaw`。

---

## 7. 下次重编：在 Mac 上交叉编译

服务器是 4 核 box，full release build 12 分钟+，期间 load 飙到 5.0 影响 daemon 响应。Mac 这边一般更快也更闲。

### 7.1 一次性准备

```bash
# 装目标三元组
rustup target add x86_64-unknown-linux-gnu

# 选一个交叉编译器
brew install zig                # cargo-zigbuild 用（无 docker，更轻）
cargo install cargo-zigbuild

# 或者
brew install cross              # 需要 docker
```

### 7.2 编译

```bash
cd /Users/qingjie.du/HDD/d-zeroclaw
cargo zigbuild --release \
  --target x86_64-unknown-linux-gnu \
  --features browser-native \
  --bin zeroclaw
# 产物：target/x86_64-unknown-linux-gnu/release/zeroclaw
```

### 7.3 部署

```bash
ssh dqj@192.168.4.39 'cp ~/.cargo/bin/zeroclaw ~/.cargo/bin/zeroclaw.bak.$(date +%Y%m%d-%H%M%S)'
ssh dqj@192.168.4.39 'systemctl --user stop zeroclaw'
scp target/x86_64-unknown-linux-gnu/release/zeroclaw \
    dqj@192.168.4.39:~/.cargo/bin/zeroclaw
ssh dqj@192.168.4.39 'systemctl --user start zeroclaw && systemctl --user status zeroclaw --no-pager | head -10'
```

### 7.4 注意事项

- 交叉编译可能撞到本地缺 sysroot 的 native crate（如 `aws-lc-sys`）。`cargo zigbuild` 通常能搞定，`cross` 则用 docker 镜像搞定一切。
- 如果只改了少量代码、服务器上 `target/` 还在，**增量编译可能比交叉编译快**——按需取舍。
- 可以加一个本地脚本固化下来，省得每次想流程：

```bash
# scripts/build-and-deploy-zeroclaw.sh（建议新建）
set -euo pipefail
HOST=dqj@192.168.4.39
cd "$(git rev-parse --show-toplevel)"
cargo zigbuild --release --target x86_64-unknown-linux-gnu \
  --features browser-native --bin zeroclaw
ssh "$HOST" "cp ~/.cargo/bin/zeroclaw ~/.cargo/bin/zeroclaw.bak.\$(date +%Y%m%d-%H%M%S) && systemctl --user stop zeroclaw"
scp target/x86_64-unknown-linux-gnu/release/zeroclaw "$HOST:~/.cargo/bin/zeroclaw"
ssh "$HOST" "systemctl --user start zeroclaw && systemctl --user status zeroclaw --no-pager | head -10 && timeout 20 ~/.cargo/bin/zeroclaw doctor | tail -5"
```

---

## 8. 易错点 / 反模式

- **被 agent 自己的诊断带偏**：当 agent 说"换个 backend"时，先确认那个 backend 真能达成你的目标。`agent_browser` 和 `computer_use` 都不能挂到现成的 9222 Chrome。
- **把 `~/zeroclaw-build*/target/release/zeroclaw` 当成正在运行的二进制**：`ps -ef | grep zeroclaw daemon` 看真实路径，多半是 `~/.cargo/bin/zeroclaw`。
- **只编 binary 但 `--features browser-native` 漏了**：默认 features 包含它，但 `cargo install` / 自定义构建很容易 `--no-default-features` 掉。最直观的指纹是 `strings | grep -c fantoccini`。
- **ChromeDriver 起着但 Chrome 不在 9222**：CDP attach 的前提是 Chrome 必须先起且参数正确。`curl http://127.0.0.1:9222/json/version` 是最快的健康检查。
- **`/tmp/chrome-debug` 重启即丢**：登录态会消失。要持久就换路径。
- **直接在服务器上跑 `cargo build`**：4 核 box 慢且抢资源。改在 Mac 上交叉编译。
- **macOS 自带 rsync 太老**：`--info=stats1` 不识别，用 `--stats`，或 `brew install rsync` 装新版。

---

## 9. 速查表

| 想做的事 | 命令 |
|---|---|
| 看正在跑的 zeroclaw 二进制路径 | `ssh dqj@192.168.4.39 'ps -ef \| grep "zeroclaw daemon" \| grep -v grep'` |
| 验 `browser-native` 是否编入 | `strings <binary> \| grep -c -i fantoccini` |
| 看 `[browser]` 配置 | `awk '/^\[browser\]/{f=1;next} /^\[/{f=0} f' ~/.zeroclaw/config.toml` |
| 9222 Chrome 健康 | `curl -s --max-time 3 http://127.0.0.1:9222/json/version` |
| daemon 状态 | `systemctl --user status zeroclaw --no-pager` |
| 全面体检 | `~/.cargo/bin/zeroclaw doctor` |
| 重启 daemon | `systemctl --user restart zeroclaw` |
| 实时日志 | `journalctl --user -u zeroclaw -f` |

---

## 10. 关键源码引用（master 分支）

- 后端枚举与解析：[`crates/zeroclaw-tools/src/browser.rs:99-111`](crates/zeroclaw-tools/src/browser.rs#L99-L111)
- 后端解析与回退逻辑：[`crates/zeroclaw-tools/src/browser.rs:341-417`](crates/zeroclaw-tools/src/browser.rs#L341-L417)
- `cfg(not(feature = "browser-native"))` 报错分支：[`crates/zeroclaw-tools/src/browser.rs:703-707`](crates/zeroclaw-tools/src/browser.rs#L703-L707)
- `native_cdp_address` schema 与文档：[`crates/zeroclaw-config/src/schema.rs:2743-2750`](crates/zeroclaw-config/src/schema.rs#L2743-L2750)
- Cargo features：[`Cargo.toml:327-346`](Cargo.toml#L327-L346)、[`crates/zeroclaw-tools/Cargo.toml:23,47`](crates/zeroclaw-tools/Cargo.toml#L23)
