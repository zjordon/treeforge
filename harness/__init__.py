"""TreeForge 蒸馏层（harness）。

复刻 Browser-BC 的纯标准库哲学：零运行时依赖（Pydantic 是唯一例外，用于结构化数据模型）。

五阶段管线（详见各模块 docstring）：

    ① ADAPT     adapter.py     原始 trace → NormalizedTrack
    ② ATOMIZE   atomizer.py    track → Segment[]
    ③ CLASSIFY  classifier.py  Segment → domain::capacity（增量一致性）
    ④ BUCKET    bucketer.py    按 capacity 归桶
    ⑤ DISTILL   distiller.py   桶 → SkillCard（站点特定知识卡，核心分叉点）

检索（registry.py）本期可先空实现——TreeWalker 用文件注入，不需要 MCP 检索。
"""

__version__ = "0.1.0"
