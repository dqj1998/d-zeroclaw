from pathlib import Path
import re
import shutil
import time

path = Path.home() / ".zeroclaw" / "config.toml"
text = path.read_text()
backup = path.with_name(f"config.toml.bak.{time.strftime('%Y%m%d-%H%M%S')}.financebot-localhost-fix")
shutil.copy2(path, backup)

block_pattern = re.compile(r"(?ms)^\[agents\.FinanceBot\]\n(?P<body>.*?)(?=^\[|\Z)")
match = block_pattern.search(text)
if not match:
    raise SystemExit("FinanceBot block not found")
body = match.group("body")

new_tools = 'allowed_tools = ["shell", "file_read", "file_write", "file_edit", "glob_search", "content_search", "web_fetch", "calculator", "memory_store", "memory_recall", "browser", "browser_open", "llm_task", "weather", "knowledge-base"]'
body, tools_count = re.subn(
    r'^allowed_tools = \[.*?\]$',
    new_tools,
    body,
    count=1,
    flags=re.M,
)
if tools_count != 1:
    raise SystemExit("FinanceBot allowed_tools line not found")

new_prompt = '''system_prompt = """
你是一个专业的金融财务助手 FinanceBot。你的目标是帮助用户利用金融手段达成财务目标。
你可以提供投资建议、财务规划、市场分析和预算管理等方面的支持。
在与用户交流时，请保持专业、客观，并始终以用户的财务健康为首位。
你可以使用搜索、计算、存储记忆以及本机 shell / 文件工具来辅助你的分析。
当前频道环境：Discord (ID: 1500810266546278460)
你当前运行在主机 192.168.4.39（/home/dqj）上。提到 192.168.4.39 时，这就是你自己所在的机器，不是需要额外 SSH 登录的远端主机。
当任务涉及 192.168.4.39 上的文件、进程、服务、日志或 QuanTrader 程序时，直接使用 shell、file_read、file_write、file_edit、glob_search、content_search 在本机执行，不要要求用户先 SSH 到 192.168.4.39。
处理与本机部署相关的问题前，优先使用 memory_recall 检索历史事实，并在需要时读取 ~/.zeroclaw/workspace/MEMORY_SNAPSHOT.md 以确认主机、路径和部署上下文。
如果用户给出的路径与当前机器实际目录不一致，先在本机验证真实路径，再继续处理，不要因为路径猜测而要求用户 SSH。"""'''
body, prompt_count = re.subn(
    r'system_prompt = """\n.*?"""',
    new_prompt,
    body,
    count=1,
    flags=re.S,
)
if prompt_count != 1:
    raise SystemExit("FinanceBot system_prompt block not found")

new_text = text[:match.start("body")] + body + text[match.end("body"):]
path.write_text(new_text)
print(f"updated={path}")
print(f"backup={backup}")
