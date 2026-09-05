"""host key（domain-skills 目录索引键）——TreeForge 产出侧与 TreeWalker 消费侧的统一 key 语义。

【契约】产物目录 / registry / 任务卡的 host key = URL 的 hostname；URL 显式带端口时为
``host_port``（``_`` 连接——Windows 目录名不能含 ``:``）。即 TreeWalker
``src/tree_walker/browser/url_utils.py::extract_host_with_port`` 的语义：本模块是其
纯函数逻辑的复制（stdlib urlparse，零依赖），两端逐字对齐。

例：``http://localhost:7780/admin`` → ``localhost_7780``；
``https://member.bilibili.com`` → ``member.bilibili.com``（无端口保持裸 hostname）。

【为什么】P4 产物曾按裸 hostname 索引（``localhost:7780`` 蒸出 ``domain-skills/localhost/``），
而 TreeWalker 按端口限定 key 读取（``localhost_7780``）——本机服务的卡对消费侧**静默**
不可见（找不到目录 = 不注入 = 不报错）。详见 ``docs/task-skill-loading-design.md`` §十一
（issue #9）。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# key 尾部的端口后缀（``_<纯数字>``）——extract_host_with_port 生成，bare_hostname 反解
_PORT_SUFFIX_RE = re.compile(r"_\d+$")


def extract_host_with_port(url: str | None) -> str | None:
    """URL → domain-skills 目录 key：``host`` 或 ``host_port``。

    - URL 显式带端口（含显式默认端口 ``http://x:80/``——与 TreeWalker 逐字对齐，
      ``parsed.port`` 为真即加后缀）→ ``host_port``；无端口保持裸 hostname。
    - schemeless 输入（``localhost:7780/x``）补 ``//`` 前缀再解析。
    - 垃圾输入（空 / hostname 缺失 / 含空格 / 端口非数字）返 None。
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host and "://" not in url:
            parsed = urlparse("//" + url)
            host = parsed.hostname
        if not host or " " in host:
            return None
        port = parsed.port
        return f"{host}_{port}" if port else host
    except (ValueError, TypeError):
        return None


def bare_hostname(host_key: str) -> str:
    """key → 裸 hostname（剥掉 ``_<port>`` 尾巴）；无端口后缀的原样返回。

    只剥「下划线 + 纯数字」结尾（extract_host_with_port 只会生成这种后缀）。
    用途：① ADAPT 把 trace 顶层 host 字段（采集期写的裸 hostname 旧形）与事件 URL
    派生的 key 对账（同站则升级为端口限定 key）；② serve host 模式对存量裸
    hostname captures 的双匹配。
    """
    if not host_key:
        return host_key
    return _PORT_SUFFIX_RE.sub("", host_key)
