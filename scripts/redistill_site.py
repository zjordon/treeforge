"""按任务卡逐个重蒸，重建站点级 skill（P4 配套工具）。

用途：站点级产物形态升级 / 旧卡损坏后的重建——按 host 的每张任务卡
（``tasks/<slug>/_task.json``）逐个重蒸：

  - 任务级：每张卡带**原任务描述**重蒸，slug 稳定化复用原 slug 覆盖刷新（不混任务）
  - 站点级：逐轮增量累积（以旧卡为基线，压缩不丢主题）；**首个成功轮 fresh**
    丢弃旧形态卡从头来
  - 每张任务卡用它的全部 source_traces（同任务多次重录的合并重蒸）

用法：
    uv run python scripts/redistill_site.py --host localhost --dry-run   # 先看计划
    uv run python scripts/redistill_site.py --host localhost             # 真跑（调 LLM）
    uv run python scripts/redistill_site.py --host localhost --output ./data/skills

约定：任务卡按 _task.json mtime 旧→新处理（先见的知识先进卡，累积自然）。
无 LLM_KEY 时拒绝真跑（模板模式重建无意义）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from harness import config, registry  # noqa: E402


def load_task_cards(skills_dir: Path, host: str) -> list[dict]:
    """读 host 的全部任务卡（按 _task.json mtime 旧→新）。损坏卡跳过。"""
    tasks_root = skills_dir / "domain-skills" / host / "tasks"
    if not tasks_root.is_dir():
        return []
    cards: list[dict] = []
    for meta_path in tasks_root.glob("*/_task.json"):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"  ! 跳过损坏任务卡 {meta_path.name}: {e}")
            continue
        if isinstance(data, dict):
            cards.append({"meta_path": meta_path, "mtime": meta_path.stat().st_mtime, **data})
    cards.sort(key=lambda c: c["mtime"])
    return cards


def resolve_traces(source_traces: list, slug: str) -> list[Path]:
    """把任务卡的 source_traces 解析成存在的绝对 Path（去重保序）。"""
    out: list[Path] = []
    for t in source_traces or []:
        p = Path(t)
        if not p.is_absolute():
            p = REPO_ROOT / p
        if p.is_file():
            if p not in out:
                out.append(p)
        else:
            print(f"  ! [{slug}] trace 不存在，跳过：{t}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="按任务卡逐个重蒸，重建站点级 skill")
    parser.add_argument("--host", required=True, help="站点 host（如 localhost）")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/skills"),
        help="skills 输出根目录（默认 data/skills）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印执行计划，不调 LLM")
    args = parser.parse_args()

    skills_dir = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    cards = load_task_cards(skills_dir, args.host)
    if not cards:
        print(
            f"错误：{skills_dir / 'domain-skills' / args.host / 'tasks'} 下没有任务卡。",
            file=sys.stderr,
        )
        return 2

    # 组装执行计划（trace 缺失的卡跳过）
    plan: list[tuple[str, str, list[Path]]] = []
    for c in cards:
        slug = str(c.get("slug") or c["meta_path"].parent.name)
        desc = str(c.get("task_description") or "")
        traces = resolve_traces(c.get("source_traces"), slug)
        if not traces:
            print(f"  ! [{slug}] 无可用 trace，整卡跳过")
            continue
        plan.append((slug, desc, traces))
    if not plan:
        print("错误：没有任何任务卡有可用 trace。", file=sys.stderr)
        return 2

    # 真跑必须配 LLM（模板模式重建无意义）
    if not args.dry_run:
        config.load()
        if not config.LLM_KEY:
            print("错误：未配置 LLM_KEY（.env）——重蒸需要真 LLM。", file=sys.stderr)
            return 2

    print(f"host={args.host} | 任务卡 {len(cards)} 张（可用 {len(plan)}）| 输出 {skills_dir}\n")

    from server.distill_api import (  # noqa: E402 - 延迟导入（依赖上面 sys.path 注入）
        run_distill_pipeline,
    )

    done_first = False  # 首个成功轮之前保持 fresh（首败不污染：后续仍从头来）
    failed = 0
    for i, (slug, desc, traces) in enumerate(plan, 1):
        # dry-run 不真跑，用轮次模拟 fresh 标记展示
        is_first_effective = (i == 1) if args.dry_run else not done_first
        tag = "重建(fresh)" if is_first_effective else "增量"
        print(f"[{i}/{len(plan)}] {slug} | trace×{len(traces)} | 描述: {desc[:40]!r} | {tag}")
        if args.dry_run:
            continue

        result = run_distill_pipeline(
            trace_paths=traces,
            output_dir=skills_dir,
            adapter_name="treewalker",
            fresh=not done_first,
            task_description=desc or None,
        )
        if not result.ok:
            failed += 1
            print(f"  ✗ 失败：{result.error}")
            continue
        done_first = True
        card = registry.load_card(skills_dir, args.host)
        ver = card["meta"]["distill_version"] if card else "?"
        sop_len = len(card.get("sop_md", "")) if card else 0
        print(f"  ✓ 站点卡 v{ver}（sop {sop_len:,} chars）| 任务卡 slug={result.task_slug}")

    if args.dry_run:
        print("\n（dry-run：以上为执行计划，未调 LLM。去掉 --dry-run 真跑。）")
        return 0

    card = registry.load_card(skills_dir, args.host)
    print(
        f"\n完成：失败 {failed}/{len(plan)} 轮；"
        f"站点卡 v{card['meta']['distill_version']}（sop {len(card.get('sop_md', '')):,} chars，"
        f"caps {len(card['meta'].get('capacities') or [])}）；"
        f"任务卡 {len(list((skills_dir / 'domain-skills' / args.host / 'tasks').iterdir()))} 张"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
