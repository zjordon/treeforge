"""hostkey 测试（S0b，issue #9）。

钉死两端统一的 host key 语义：hostname；显式端口 → ``host_port``。
与 TreeWalker ``extract_host_with_port`` 逐字对齐（含显式默认端口、schemeless、垃圾输入）。
"""

from __future__ import annotations

from harness.hostkey import bare_hostname, extract_host_with_port

# ---------------------------------------------------------------------------
# extract_host_with_port：key 语义
# ---------------------------------------------------------------------------


def test_key_with_explicit_port():
    assert extract_host_with_port("http://localhost:7780/admin") == "localhost_7780"
    assert extract_host_with_port("http://127.0.0.1:5173/") == "127.0.0.1_5173"


def test_key_without_port_is_bare_hostname():
    assert extract_host_with_port("https://member.bilibili.com/platform/home") == (
        "member.bilibili.com"
    )
    assert extract_host_with_port("https://bilibili.com") == "bilibili.com"


def test_key_explicit_default_port_still_suffixed():
    """显式默认端口与 TreeWalker 逐字对齐：parsed.port 为真即加后缀。"""
    assert extract_host_with_port("http://x:80/") == "x_80"
    assert extract_host_with_port("https://x:443/a") == "x_443"


def test_key_schemeless_input():
    """schemeless 输入（无 ://）补 // 前缀再解析——不把 host 误当 scheme。"""
    assert extract_host_with_port("localhost:7780/admin") == "localhost_7780"
    assert extract_host_with_port("member.bilibili.com/platform") == "member.bilibili.com"


def test_key_hostname_lowercased():
    assert extract_host_with_port("http://LocalHost:7780/") == "localhost_7780"


def test_key_garbage_inputs_return_none():
    assert extract_host_with_port(None) is None
    assert extract_host_with_port("") is None
    assert extract_host_with_port("not a url") is None  # hostname 含空格
    assert extract_host_with_port("http://x:abc/") is None  # 端口非数字


# ---------------------------------------------------------------------------
# bare_hostname：key → 裸 hostname（双匹配 / 对账用）
# ---------------------------------------------------------------------------


def test_bare_hostname_strips_port_suffix():
    assert bare_hostname("localhost_7780") == "localhost"
    assert bare_hostname("127.0.0.1_7780") == "127.0.0.1"


def test_bare_hostname_no_suffix_unchanged():
    assert bare_hostname("member.bilibili.com") == "member.bilibili.com"
    assert bare_hostname("localhost") == "localhost"
    assert bare_hostname("") == ""


def test_bare_hostname_roundtrip_with_extract():
    """bare_hostname(extract(url)) 只在 key 带 _<digits> 尾巴时才与原 key 不同。"""
    for url, key in [
        ("http://localhost:7780/", "localhost_7780"),
        ("https://member.bilibili.com/", "member.bilibili.com"),
    ]:
        assert extract_host_with_port(url) == key
    assert bare_hostname("localhost_7780") != "localhost_7780"
    assert bare_hostname("member.bilibili.com") == "member.bilibili.com"
