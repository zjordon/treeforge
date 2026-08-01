/**
 * 通用采集信封（所有场景共用）—— Python 侧 treeforge/capture/backend.py 的契约镜像。
 *
 * 扩展 POST /ingest 的外层结构。后端按 scenario 路由：
 *   - 'distill' → TreeForge 采集落盘 pipeline（collector.py）
 *   - 'replay'  → 留接口（TreeWalker 迁入时实现）
 *
 * 详见 docs/p2/README.md 3.2.5 节协议设计。
 */

/** 采集场景 */
export type CaptureScenario = "distill" | "replay";

/** 通用采集信封（POST /ingest 的 body） */
export interface CaptureEnvelope<TPayload = unknown> {
  /** 场景标记，后端按此路由 */
  scenario: CaptureScenario;
  /** 会话 id（POST /start 返回） */
  session_id: string;
  /** 毫秒时间戳 */
  ts: number;
  /** 当前页面 URL（采集瞬间） */
  url?: string;
  /** 是否顶层 frame */
  is_top_frame?: boolean;
  /** 来源 tab id（background 从 sender.tab.id 注入，后端据此精确 attach CDP target） */
  tab_id?: number;
  /** 场景特定 payload（distill 场景见 distill-schema.ts） */
  payload: TPayload;
}

/** 后端默认地址（与 Python treeforge/capture/backend.py DEFAULT_HOST/PORT 对齐） */
export const DEFAULT_ENDPOINT = "http://127.0.0.1:8765";
