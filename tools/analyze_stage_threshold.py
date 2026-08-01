"""stage 阈值调参分析工具（P2.2.4）。

用法：
  uv run python tools/analyze_stage_threshold.py <trace.json>

读取采集产物 trace.json 的 page_context（各 stage 的 DOM 文本），
计算相邻 stage 之间的 DOM 相似度，帮你判断：
  - 哪些 stage 切换是「真阶段」（upload → publish，相似度应很低）
  - 哪些 stage 切换是「误切」（同页面滚动/面板展开，相似度应较高）
  - 阈值该定在哪个值，能区分这两类

输出：
  1. 各 stage 的 DOM 行数/字符数
  2. 相邻 stage 的 Jaccard 相似度
  3. 建议阈值（基于「真阶段」和「误切」的分界）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def dom_similarity(text_a: str, text_b: str) -> float:
    """Jaccard 行集合相似度（对齐 treeforge/capture/stage.py）。"""
    if not text_a and not text_b:
        return 1.0
    if not text_a or not text_b:
        return 0.0
    set_a = set(text_a.splitlines())
    set_b = set(text_b.splitlines())
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 1.0


def main(trace_path: str) -> int:
    p = Path(trace_path)
    if not p.is_file():
        print(f"错误：文件不存在：{trace_path}", file=sys.stderr)
        return 1

    data = json.loads(p.read_text(encoding="utf-8"))
    page_context: dict[str, str] = data.get("page_context", {})
    events = data.get("events", [])

    if not page_context:
        print("trace.json 无 page_context，无法分析", file=sys.stderr)
        return 1

    stages = list(page_context.keys())
    print(f"=== {p.name} ===")
    print(f"stage 数：{len(stages)}")
    print(f"event 数：{len(events)}")
    print()

    # 1. 各 stage 的规模
    print("--- 各 stage 规模 ---")
    for s in stages:
        text = page_context[s] or ""
        lines = len(text.splitlines())
        print(f"  {s:<15} {len(text):>6} 字符  {lines:>4} 行")
    print()

    # 2. 相邻 stage 相似度
    print("--- 相邻 stage 相似度（Jaccard）---")
    sims = []
    for i in range(1, len(stages)):
        prev, curr = stages[i - 1], stages[i]
        sim = dom_similarity(page_context[prev], page_context[curr])
        sims.append((prev, curr, sim))
        bar = "█" * int(sim * 40)
        print(f"  {prev:<12} → {curr:<12} {sim:.3f}  {bar}")
    print()

    # 3. 每个 event 所属 stage 分布
    print("--- event 的 stage 分布 ---")
    from collections import Counter

    stage_counts = Counter(e.get("stage", "?") for e in events)
    for s, c in stage_counts.most_common():
        print(f"  {s:<15} {c} 个事件")
    print()

    # 4. 建议阈值
    if len(sims) >= 2:
        sim_values = [s for _, _, s in sims]
        sim_min = min(sim_values)
        sim_max = max(sim_values)
        print("--- 建议阈值 ---")
        print(f"  相似度范围：{sim_min:.3f} ~ {sim_max:.3f}")
        # 如果范围跨度大（说明有真阶段和误切的分界），建议取中点
        if sim_max - sim_min > 0.2:
            suggested = (sim_min + sim_max) / 2
            print(f"  存在分界（跨度 {sim_max - sim_min:.3f} > 0.2）")
            print(f"  建议阈值：{suggested:.2f}（区分真阶段和误切）")
            print(f"  判定：相似度 < {suggested:.2f} 算新阶段")
            # 列出哪些会被合并
            print("  按此阈值，以下切换会被合并（视为同阶段）：")
            for prev, curr, sim in sims:
                if sim >= suggested:
                    print(f"    {prev} → {curr}（相似度 {sim:.3f} ≥ {suggested:.2f}）")
            print("  以下切换会保留（视为真阶段切换）：")
            for prev, curr, sim in sims:
                if sim < suggested:
                    print(f"    {prev} → {curr}（相似度 {sim:.3f} < {suggested:.2f}）")
        else:
            print(f"  相似度集中（跨度 {sim_max - sim_min:.3f} ≤ 0.2），难以区分")
            print("  可能需要结合其他信号（如 URL、关键元素出现）而非纯相似度")

    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法：uv run python tools/analyze_stage_threshold.py <trace.json>")
        sys.exit(2)
    raise SystemExit(main(sys.argv[1]))
