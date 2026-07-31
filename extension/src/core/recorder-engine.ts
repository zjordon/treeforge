/**
 * 通用采集骨架（机制层）—— docs/p2/README.md 3.2.5 节。
 *
 * 借鉴 TreeWalker recording_extension/capture/action-recorder.ts 的骨架
 * （on() 工厂 + cleanup + input/scroll 去抖），但剥离重放定制（策略可插拔）。
 *
 * 骨架负责「机制」（事件绑定/去抖/emit），策略负责「决策」（找祖先/跳过/字段）。
 * 详见 core/strategy.ts 的 CollectionStrategy 接口。
 *
 * 【P2.3.1 最小版】先支持 click + input（带去抖）+ keydown(Enter) + scroll(去抖)。
 * P2.3.2 补 IME / contenteditable observer / 更多字段。
 */

import type { CollectionStrategy } from "./strategy";
import type { DistillEventPayload } from "../shared/distill-schema";

/** 事件发射回调（recorder-engine 把构建好的 payload 通过此回调发出去） */
export type EmitFn = (payload: DistillEventPayload) => void;

/** scroll 去抖时长（毫秒）—— 通用骨架常量，策略可覆盖 */
const SCROLL_IDLE_MS = 500;

/** RecorderEngine：装配事件监听 + 去抖，按策略决策采什么。 */
export class RecorderEngine {
  private cleanupFns: Array<() => void> = [];
  private installed = false;

  // input 去抖状态
  private pendingInputTimer: ReturnType<typeof setTimeout> | null = null;
  private pendingInputTarget: Element | null = null;
  private isComposing = false; // IME 组合输入标志

  // scroll 去抖状态
  private scrollY = 0;
  private scrollTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly strategy: CollectionStrategy,
    private readonly emit: EmitFn,
  ) {}

  /** 装配事件监听（幂等） */
  install(): void {
    if (this.installed) return;
    this.installed = true;

    // click（无去抖，click 本身是离散动作）
    this.on("click", (e) => this.onClick(e), true);

    // input（去抖合并）
    this.on("input", (e) => this.onInput(e), true);

    // compositionstart/end（IME 组合输入抑制中间值，P2.3.2 完善）
    this.on("compositionstart", () => {
      this.isComposing = true;
    });
    this.on("compositionend", (e) => {
      this.isComposing = false;
      // compositionend 后立即 flush 当前 input
      if (this.pendingInputTarget) {
        this.flushInput();
      }
    });

    // keydown（只采有意义的关键键，如 Enter）
    this.on("keydown", (e) => this.onKeydown(e as KeyboardEvent), true);

    // scroll（wheel 累计 + 空闲去抖）
    this.on("wheel", (e) => this.onWheel(e as WheelEvent), { passive: true });
  }

  /** 卸载所有监听 + flush 残留去抖 */
  uninstall(): void {
    if (!this.installed) return;
    this.installed = false;
    this.flushInput();
    this.flushScroll();
    for (const fn of this.cleanupFns) {
      try {
        fn();
      } catch {
        /* ignore */
      }
    }
    this.cleanupFns = [];
  }

  // ---- 事件处理（机制层，回调策略做决策）----

  private onClick(e: Event): void {
    const raw = e.target as Element | null;
    if (!raw) return;
    // 策略决策：找可交互祖先 + 是否跳过
    const target = this.strategy.resolveClickTarget(raw);
    if (!target) return; // 策略决定跳过（distill 保留误操作，replay 可能严格过滤）
    const payload = this.strategy.buildClickPayload(target);
    this.emit(payload);
  }

  private onInput(e: Event): void {
    if (this.isComposing) return; // IME 组合中不采中间值
    const target = e.target as Element | null;
    if (!target) return;
    // 去抖：同目标连续输入合并
    if (this.pendingInputTarget !== target) {
      this.flushInput(); // 切目标先冲刷前一个
    }
    this.pendingInputTarget = target;
    // 重置去抖定时器
    if (this.pendingInputTimer) clearTimeout(this.pendingInputTimer);
    this.pendingInputTimer = setTimeout(() => this.flushInput(), this.strategy.inputCoalesceMs);
  }

  private flushInput(): void {
    if (this.pendingInputTimer) {
      clearTimeout(this.pendingInputTimer);
      this.pendingInputTimer = null;
    }
    const target = this.pendingInputTarget;
    this.pendingInputTarget = null;
    if (!target) return;
    const value = this.strategy.readInputValue(target);
    const payload = this.strategy.buildInputPayload(target, value);
    this.emit(payload);
  }

  private onKeydown(e: KeyboardEvent): void {
    // 只采有意义的关键键（Enter/Escape/Tab），其余交给 input 去抖
    // 修饰键组合不单独采（如 Ctrl+S，交给上层判断）
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    const key = e.key;
    if (!["Enter", "Escape", "Tab"].includes(key)) return;

    // Enter 等键先 flush 残留 input（避免把一次输入切成多步）
    this.flushInput();

    const target = e.target as Element | null;
    const payload = this.strategy.buildKeydownPayload(key, target);
    if (payload) this.emit(payload);
  }

  private onWheel(e: WheelEvent): void {
    // 累计 deltaY，500ms 空闲后 flush
    this.scrollY += e.deltaY;
    if (this.scrollTimer) clearTimeout(this.scrollTimer);
    this.scrollTimer = setTimeout(() => this.flushScroll(), SCROLL_IDLE_MS);
  }

  private flushScroll(): void {
    if (this.scrollTimer) {
      clearTimeout(this.scrollTimer);
      this.scrollTimer = null;
    }
    if (this.scrollY === 0) return;
    // 归一化滚动量（粗略：每 100px 算 1 单位，clamp 1-10）
    const amount = Math.max(1, Math.min(10, Math.round(Math.abs(this.scrollY) / 100)));
    const payload = this.strategy.buildScrollPayload(amount);
    this.emit(payload);
    this.scrollY = 0;
  }

  // ---- 监听绑定工具（on/cleanup，借鉴 TreeWalker action-recorder.ts:372-376）----

  /**
   * 绑定事件监听，返回 cleanup 函数。capture 控制捕获/冒泡阶段。
   * 用 capture=true 优先拿到事件（避免页面 stopPropagation 拦截）。
   */
  private on(
    type: string,
    handler: (e: Event) => void,
    options: boolean | AddEventListenerOptions = false,
  ): void {
    document.addEventListener(type, handler, options);
    this.cleanupFns.push(() => document.removeEventListener(type, handler, options));
  }
}
