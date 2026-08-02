/**
 * 采集策略接口（可插拔）—— docs/p2/README.md 3.2.5 节「通用骨架 vs 可插拔策略」。
 *
 * recorder-engine.ts 是通用骨架（监听/去抖/emit），在每个决策点回调策略。
 * 各场景各实现一套：
 *   - strategies/distill/index.ts  蒸馏场景（P2.3.1 实现）
 *   - strategies/replay/index.ts   重放场景（TreeWalker 迁入时实现）
 */

import type { DistillEventPayload } from "../shared/distill-schema";

/**
 * 采集策略接口。recorder-engine 在决策点回调这些方法。
 *
 * 设计原则：策略只做「决策」，不做「机制」。
 * 机制（事件绑定/去抖/emit）由 recorder-engine 负责，策略只回答：
 *   - 这个 click 该采吗？采哪个元素？
 *   - input 要去抖合并多久？
 *   - 这个元素采哪些字段？
 */
export interface CollectionStrategy {
  /** 场景名（与 CaptureEnvelope.scenario 对齐） */
  readonly scenario: "distill" | "replay";

  // ---- click 策略 ----

  /**
   * 解析 click 的目标元素（如找可交互祖先）。
   * 返回 null 表示跳过这个 click。
   * distill 策略：可更宽松（保留误操作）；replay 策略：严格找可交互祖先。
   */
  resolveClickTarget(rawTarget: Element): Element | null;

  // ---- input 策略 ----

  /** input 去抖合并时长（毫秒）。连续输入在此窗口内合并成一次。 */
  readonly inputCoalesceMs: number;

  /** 读取输入元素的当前值 */
  readInputValue(target: Element): string;

  // ---- 字段提取 ----

  /** 从目标元素提取 raw_attrs（策略决定采哪些字段） */
  extractAttrs(target: Element): Record<string, string>;

  /** 算元素的人类可读标签（作为 payload.target） */
  computeTargetLabel(target: Element): string;

  // ---- 事件构建 ----

  /** 把采集到的原始信息构建成 payload（策略决定 payload 形状） */
  buildClickPayload(target: Element): DistillEventPayload;
  buildInputPayload(target: Element, value: string): DistillEventPayload;
  buildScrollPayload(amount: number): DistillEventPayload;
  buildNavigatePayload(url: string): DistillEventPayload;

  // ---- P3.6 扩词（迁自 TreeWalker）：新事件类型的 payload 构建 ----

  /**
   * <select> 的 change → 选中项 value。
   * 返回 null 表示策略决定跳过（distill 保留，replay 可能严格过滤）。
   */
  buildSelectPayload(target: Element, value: string): DistillEventPayload | null;

  /**
   * <input type=file> 的 change → 文件名 + upload_ctx 语义身份。
   * files 为空（用户取消选择）时调用方应跳过；此处假定 files 非空。
   */
  buildUploadPayload(
    input: HTMLInputElement,
    fileName: string,
  ): DistillEventPayload;

  /**
   * 快捷键 / 命名非打印键 → send_keys。
   * RecorderEngine 已判定该键属于 send_keys（修饰键组合或命名键），key 是组合后的
   * 字符串（如 "Control+S" "Enter" "F5"）。返回 null 表示策略跳过。
   */
  buildSendKeysPayload(key: string, target: Element | null): DistillEventPayload | null;
}
