/**
 * 通用采集骨架（机制层）—— docs/p2/README.md 3.2.5 节。
 *
 * 借鉴 TreeWalker recording_extension/capture/action-recorder.ts 的骨架
 * （on() 工厂 + cleanup + input/scroll 去抖），但剥离重放定制（策略可插拔）。
 *
 * 骨架负责「机制」（事件绑定/去抖/emit），策略负责「决策」（找祖先/跳过/字段）。
 * 详见 core/strategy.ts 的 CollectionStrategy 接口。
 *
 * 【P2.3.1 最小版】click + input（去抖）+ keydown(Enter) + scroll(去抖)。
 * 【P3.6 扩词】迁自 TreeWalker：
 *   - change on <select> → select_dropdown；change on <input type=file> → upload_file
 *   - keydown 扩成 send_keys（修饰键组合 + 命名非打印键，含 Enter/Tab/Escape/F1-12）
 *   - contenteditable 富文本用 MutationObserver 观察（标准 input 事件不派发，如 Slate）
 *   - keyup 也喂 input 合并（对齐 TreeWalker）
 *   - 副作用观察 hook：emit 后通知 side-effect-observer 开 1s 窗口（setActionHook 注入）
 */

import type { CollectionStrategy } from "./strategy";
import type { DistillEventPayload } from "../shared/distill-schema";

/** 事件发射回调（recorder-engine 把构建好的 payload 通过此回调发出去） */
export type EmitFn = (payload: DistillEventPayload) => void;

/** scroll 去抖时长（毫秒）—— 通用骨架常量，策略可覆盖 */
const SCROLL_IDLE_MS = 500;

/** 命名非打印键（send_keys 录这些；可打印字符归 input）。F1-F12 用正则另判。 */
const NAMED_KEYS = new Set([
  "Enter",
  "Tab",
  "Escape",
  "Backspace",
  "Delete",
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "Home",
  "End",
  "PageUp",
  "PageDown",
]);

/** 编辑键（无修饰符时归 input 最终值，不发 send_keys）：删除/移动光标效果已反映在
 *  后续 input 事件的 value 里；单独发会 flushInput 打断 inputCoalesceMs 合并。 */
const EDIT_KEYS = new Set([
  "Backspace",
  "Delete",
  "ArrowLeft",
  "ArrowRight",
  "ArrowUp",
  "ArrowDown",
  "Home",
  "End",
  "PageUp",
  "PageDown",
]);

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

  // contenteditable 富文本观察器（Slate 等，标准 input 不派发）
  private ceObservers: MutationObserver[] = [];
  private ceDocObserver: MutationObserver | null = null;

  /** 副作用观察 hook（side-effect-observer 注入）：emit 后通知，开 1s 观察窗口。
   *  可选——不注入则不观察。 */
  private actionHook: ((ts: number) => void) | null = null;

  constructor(
    private readonly strategy: CollectionStrategy,
    private readonly emit: EmitFn,
  ) {}

  /** 注入副作用观察 hook（side-effect-observer 用）。emit 后调，传动作时间戳。 */
  setActionHook(fn: ((ts: number) => void) | null): void {
    this.actionHook = fn;
  }

  /** 装配事件监听（幂等） */
  install(): void {
    if (this.installed) return;
    this.installed = true;

    // click（无去抖，click 本身是离散动作）
    this.on("click", (e) => this.onClick(e), true);

    // input（去抖合并）+ keyup（对齐 TreeWalker：keyup 也喂 input 合并）
    this.on("input", (e) => this.onInput(e), true);
    this.on("keyup", (e) => this.onInput(e), true);

    // compositionstart/end（IME 组合输入抑制中间值）
    this.on("compositionstart", () => {
      this.isComposing = true;
    });
    this.on("compositionend", () => {
      this.isComposing = false;
      // compositionend 后立即 flush 当前 input
      if (this.pendingInputTarget) {
        this.flushInput();
      }
    });

    // keydown（P3.6：扩成 send_keys——修饰键组合 + 命名非打印键，含 Enter/Tab/Escape/F1-12）
    this.on("keydown", (e) => this.onKeydown(e as KeyboardEvent), true);

    // change（P3.6：<select> → select_dropdown；<input type=file> → upload_file）
    this.on("change", (e) => this.onChange(e), true);

    // scroll（wheel 累计 + 空闲去抖）
    this.on("wheel", (e) => this.onWheel(e as WheelEvent), { passive: true });

    // contenteditable 富文本（P3.6：Slate 等标准 input 不派发，用 MutationObserver 观察）
    this.installContentEditableObservers();
  }

  /** 卸载所有监听 + flush 残留去抖 */
  uninstall(): void {
    if (!this.installed) return;
    this.installed = false;
    this.flushInput();
    this.flushScroll();
    this.ceObservers.forEach((o) => o.disconnect());
    this.ceObservers = [];
    this.ceDocObserver?.disconnect();
    this.ceDocObserver = null;
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

  private emitPayload(payload: DistillEventPayload): void {
    this.emit(payload);
    // 通知副作用观察器：动作已发，开 1s 观察窗口（P3.6）
    this.actionHook?.(Date.now());
  }

  private onClick(e: Event): void {
    const raw = e.target as Element | null;
    if (!raw) return;
    // 跳过 file input 的 click（多是上传按钮 JS 触发的 input.click()，change 会录 upload_file）
    if (raw.tagName === "INPUT" && (raw.getAttribute("type") || "").toLowerCase() === "file") return;
    // 策略决策：找可交互祖先 + 是否跳过
    const target = this.strategy.resolveClickTarget(raw);
    if (!target) return; // 策略决定跳过（distill 保留误操作，replay 可能严格过滤）
    this.flushInput(); // click 前先冲刷残留 input（避免把一次输入切成多步）
    const payload = this.strategy.buildClickPayload(target);
    this.emitPayload(payload);
  }

  private onInput(e: Event): void {
    if (this.isComposing) return; // IME 组合中不采中间值
    const target = e.target as Element | null;
    if (!target) return;
    // file/radio/checkbox input 不走文本输入（file→upload_file，radio/checkbox→click）
    if (target.tagName === "INPUT") {
      const t = (target.getAttribute("type") || "").toLowerCase();
      if (t === "file" || t === "radio" || t === "checkbox") return;
    }
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
    this.emitPayload(payload);
  }

  private onKeydown(e: KeyboardEvent): void {
    if (e.repeat) return;
    const key = e.key;
    // 裸修饰键按下（如只按 Shift）不发
    if (key === "Control" || key === "Alt" || key === "Shift" || key === "Meta") return;

    const hasMod = e.ctrlKey || e.altKey || e.metaKey; // Shift 不计入 send_keys 触发
    // 编辑键（Backspace/Delete/方向/Home/End/PageUp/Down）无修饰符时归 input 最终值
    if (!hasMod && EDIT_KEYS.has(key)) return;

    // send_keys：修饰键组合 或 命名非打印键（含 F1-F12）
    const isNamed = NAMED_KEYS.has(key) || /^F([1-9]|10|11|12)$/.test(key);
    if (hasMod || isNamed) {
      // 组合键字符串：如 "Control+S" "Control+Shift+Z" "Enter" "F5"
      const mods: string[] = [];
      if (e.ctrlKey) mods.push("Control");
      if (e.altKey) mods.push("Alt");
      if (e.shiftKey && hasMod) mods.push("Shift"); // Shift 仅配合 ctrl/alt/meta 时计入
      if (e.metaKey) mods.push("Meta");
      const combo = (mods.length ? [...mods, key] : [key]).join("+");

      this.flushInput(); // send_keys 前先冲刷残留 input
      const target = e.target as Element | null;
      const payload = this.strategy.buildSendKeysPayload(combo, target);
      if (payload) this.emitPayload(payload);
      return;
    }

    // 普通可打印 / Shift+字符 / IME Process → 交给 input 去抖
  }

  private onChange(e: Event): void {
    const target = e.target as Element | null;
    if (!target) return;

    // <select> → select_dropdown
    if (target.tagName === "SELECT") {
      this.flushInput();
      const value = (target as HTMLSelectElement).value;
      const payload = this.strategy.buildSelectPayload(target, value);
      if (payload) this.emitPayload(payload);
      return;
    }

    // <input type=file> → upload_file
    if (
      target.tagName === "INPUT" &&
      (target.getAttribute("type") || "").toLowerCase() === "file"
    ) {
      const file = (target as HTMLInputElement).files?.[0];
      if (!file) return; // 用户取消选择
      this.flushInput();
      const payload = this.strategy.buildUploadPayload(target as HTMLInputElement, file.name);
      this.emitPayload(payload);
      return;
    }

    // 其它 change（checkbox/radio/color/date 等）：distill 暂不单独建模，交给 click。
  }

  private onWheel(e: WheelEvent): void {
    // 方向反转：先冲刷上一段（避免 up/down 互相抵消），再累计新方向（对齐 TreeWalker）
    if (this.scrollY !== 0 && Math.sign(e.deltaY) !== Math.sign(this.scrollY)) {
      this.flushScroll();
    }
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
    this.emitPayload(payload);
    this.scrollY = 0;
  }

  // ---- contenteditable 富文本观察（P3.6，迁自 TreeWalker）----

  /** 观察 [contenteditable] 元素的 textContent 变化（Slate 等标准 input 不派发） */
  private installContentEditableObservers(): void {
    const observeCe = (el: Element): void => {
      if (this.ceObservers.some((o) => (o as unknown as { _el?: Element })._el === el)) return;
      const mo = new MutationObserver(() => {
        if (this.isComposing) return; // IME composing 中——等 compositionend 后的最终值
        // 复用 input 去抖路径（target-switch flush + coalesce）
        if (this.pendingInputTarget !== el) this.flushInput();
        this.pendingInputTarget = el;
        if (this.pendingInputTimer) clearTimeout(this.pendingInputTimer);
        this.pendingInputTimer = setTimeout(() => this.flushInput(), this.strategy.inputCoalesceMs);
      });
      (mo as unknown as { _el?: Element })._el = el;
      mo.observe(el, { subtree: true, characterData: true, childList: true });
      this.ceObservers.push(mo);
    };

    document.querySelectorAll("[contenteditable]").forEach(observeCe);

    // 监听动态新增的 contenteditable（SPA 弹出编辑器）
    this.ceDocObserver = new MutationObserver((muts) => {
      for (const m of muts) {
        for (const n of Array.from(m.addedNodes)) {
          if (n.nodeType === Node.ELEMENT_NODE) {
            const el = n as Element;
            if (el.matches?.("[contenteditable]")) observeCe(el);
            el.querySelectorAll?.("[contenteditable]").forEach(observeCe);
          }
        }
      }
    });
    this.ceDocObserver.observe(document.body, { subtree: true, childList: true });
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
