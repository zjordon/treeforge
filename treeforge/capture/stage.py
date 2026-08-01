"""阶段切换判定 + 自动命名（P2.2.3）。

【设计依据】docs/p2/README.md 3.2.4 节。
【目标】采集时确定每个事件属于哪个页面阶段（stage），消除事后启发式推断的 ? 标记。
  stage 是 page_context 的 key，event.stage 指向它（1:N，SPA 多步共享一快照）。

判定信号（三路组合，SPA 友好）：
  1. URL 变化（path 段变了）→ 多页表单站点
  2. DOM 文本变化率超阈值（Jaccard 行集合相似度 < 阈值）→ SPA 阶段切换
  3. 导航事件（整页跳转）→ 传统多页站点

命名（DOM 特征增强 + URL 兜底）：
  - 先跑 DOM 特征检测：命中特异性 DOM 特征用语义名（如 upload-cover / edit-cover）
  - 未命中时退化 URL path 段（如 /platform/upload → upload）
  - 都失败用 stage_N（序号）
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# DOM 相似度阈值：低于此值视为阶段切换。
# P2.2.4 用 bilibili 实测数据校准（data/captures/0ddbaa84）：
#   真阶段切换（不同页面/大变）相似度 0.04~0.25
#   同页误切（滚动/面板展开）相似度 0.41~0.62
#   0.33 卡在两类之间，8 stage 合并到 5 stage。
DEFAULT_SIMILARITY_THRESHOLD = 0.33

# 命名时跳过的 URL path 段（无语义的通用段）
_PATH_SKIP_SEGMENTS = frozenset({"api", "platform", "www", "app", "v1", "v2"})

# stage 语义特征检测：DOM 文本含特异性特征时用语义名，而非 URL 段。
# 按特异性排序，首个命中即用。特征来自 bilibili 真机数据校准
# （data/captures/72111447 / 0ddbaa84 快照分析）：
#   <canvas> 仅封面裁剪编辑器出现；accept=image/png 仅封面 modal；accept=.mp4 在所有上传页都有。
_STAGE_FEATURES: tuple[tuple[re.Pattern[str], str], ...] = (
    # 封面裁剪编辑器：canvas 仅在裁剪阶段出现（最特异）
    (re.compile(r"<canvas[^>]*>"), "edit-cover"),
    # 封面上传：image/png|jpeg 输入仅在封面 modal 打开时出现
    (re.compile(r'accept=["\']?image/(?:png|jpeg)', re.IGNORECASE), "upload-cover"),
    # 视频上传表单：.mp4 输入（在所有上传子阶段都有，特异性最低）
    (re.compile(r'accept=["\']?\.?(?:mp4|flv|avi)', re.IGNORECASE), "upload-video"),
)


def detect_semantic(dom_text: str) -> str | None:
    """从 DOM 文本检测语义阶段名。命中返回语义名，未命中返回 None。

    按特异性顺序匹配 _STAGE_FEATURES，首个命中即返回。
    """
    if not dom_text:
        return None
    for pattern, name in _STAGE_FEATURES:
        if pattern.search(dom_text):
            return name
    return None


def dom_similarity(text_a: str, text_b: str) -> float:
    """计算两段 DOM 文本的 Jaccard 相似度（行集合交集/并集）。

    用行集合而非字符，因为 DOM 文本是结构化的（每行一个元素），
    行级相似度更能反映「页面结构是否大变」。
    """
    if not text_a and not text_b:
        return 1.0
    if not text_a or not text_b:
        return 0.0
    set_a = set(text_a.splitlines())
    set_b = set(text_b.splitlines())
    intersection = set_a & set_b
    union = set_a | set_b
    if not union:
        return 1.0
    return len(intersection) / len(union)


class StageTracker:
    """阶段切换判定 + 自动命名。状态在采集器实例内累积。

    用法：
        tracker = StageTracker()
        raw = tracker.detect_change(url, dom_text, is_navigation=False)
        if raw:  # 进入新阶段
            stage_name = tracker.name_stage(url, raw)
            # 存快照、分配 stage
        else:
            stage_name = tracker.current_stage  # 同阶段，继承
    """

    def __init__(self, similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> None:
        self.similarity_threshold = similarity_threshold
        self.current_stage: str | None = None
        self._last_url_path: str | None = None
        self._last_dom_text: str | None = None
        self._stage_counter = 0

    def detect_change(
        self,
        url: str,
        dom_text: str,
        is_navigation: bool = False,
    ) -> str | None:
        """判定是否进入新页面阶段。

        Args:
            url: 当前页面 URL
            dom_text: 当前 DOM 文本（element_tree_text）
            is_navigation: 是否是整页导航事件（navigate 类型）

        Returns:
            新 stage 的「原始标识」（由 name_stage 命名）；None 表示无切换（同阶段）。
        """
        # 信号 1：整页导航（多页站点，如传统表单提交后跳转）
        if is_navigation:
            return f"nav:{url}"

        # 信号 2：URL path 变化（首次访问 _last_url_path=None 也会触发，作为首阶段）
        new_path = urlparse(url).path if url else ""
        if new_path != self._last_url_path:
            self._last_url_path = new_path
            return f"url:{new_path}"

        # 信号 3：DOM 文本变化率超阈值（SPA 阶段切换）
        # 关键：只在判为切换时才更新 _last_dom_text（和上一个 stage 的 DOM 比，
        # 而非和上一个事件比）。避免连续小变化累积成大变化导致误切。
        if self._last_dom_text is not None and dom_text != self._last_dom_text:
            similarity = dom_similarity(self._last_dom_text, dom_text)
            if similarity < self.similarity_threshold:
                self._last_dom_text = dom_text  # 切换了：更新基准为新 stage 的 DOM
                return f"dom:{similarity:.2f}"
            # 未切换：不更新 _last_dom_text，下次仍和上一个 stage 的 DOM 比（避免累积漂移）
            return None

        # 首次（_last_dom_text is None）：初始化基准
        if self._last_dom_text is None:
            self._last_dom_text = dom_text
        return None

    def name_stage(self, url: str, raw_stage: str, dom_text: str = "") -> str:
        """给 detect_change 返回的原始标识命名。

        命名策略（DOM 特征优先 + URL 兜底）：
        1. DOM 特征检测：dom_text 含特异性特征（如 accept=image/png）用语义名
        2. URL path 段优先（取最后一个有意义的段）
        3. 若名字和当前 stage 相同（SPA 切换 URL 不变），用 stage_N 避免冲突
        4. 无法提取时用 stage_N（序号）

        避免冲突很重要：page_context 用 stage 名作 key，两个不同阶段同名会导致快照覆盖。

        dom_text: 当前 DOM 文本（element_tree_text），用于语义特征检测。空则跳过。
        """
        # 1. DOM 特征语义命名（优先，特异性最强）
        semantic = detect_semantic(dom_text)
        if semantic:
            return self._finalize_name(semantic)

        # 2. URL path 段命名（兜底：nav/url 是 URL 变，dom 是 SPA 切换但 URL 可能不变）
        if url:
            path = urlparse(url).path.strip("/")
            if path:
                segments = [
                    s for s in path.split("/") if s and s.lower() not in _PATH_SKIP_SEGMENTS
                ]
                if segments:
                    name = segments[-1].lower().split(".")[0]  # 清理：去掉扩展名
                    return self._finalize_name(name)

        # 3. 序号兜底
        self._stage_counter += 1
        name = f"stage_{self._stage_counter}"
        self.current_stage = name
        return name

    def _finalize_name(self, name: str) -> str:
        """处理命名冲突：与 current_stage 同名时加 _N 后缀（防 page_context 覆盖）。"""
        if name != self.current_stage:
            self.current_stage = name
            return name
        # 同名：用 _N 兜底（SPA 切换 URL 不变时，name 会等于 current_stage）
        self._stage_counter += 1
        unique = f"{name}_{self._stage_counter}"
        self.current_stage = unique
        return unique

    def force_new_stage(self, url: str, dom_text: str = "") -> str:
        """强制开新阶段（如录制开始时的首页）。

        返回命名的 stage，同时更新内部状态（current_stage + _last_url_path）。
        detect_change 后续会基于这个状态判定切换。

        dom_text: 当前 DOM 文本，用于语义特征检测（首阶段也语义化）。
        """
        # 同步 _last_url_path，避免首事件误判 URL 变化
        self._last_url_path = urlparse(url).path if url else ""
        # 1. DOM 特征语义命名（优先）
        semantic = detect_semantic(dom_text)
        if semantic:
            self.current_stage = semantic
            return semantic
        # 2. URL path 段命名
        path = urlparse(url).path.strip("/") if url else ""
        if path:
            segments = [s for s in path.split("/") if s and s.lower() not in _PATH_SKIP_SEGMENTS]
            if segments:
                name = segments[-1].lower().split(".")[0]
                self.current_stage = name
                return name
        # 3. 序号兜底
        self._stage_counter += 1
        name = f"stage_{self._stage_counter}"
        self.current_stage = name
        return name
