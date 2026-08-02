// 副作用观察器（迁自 TreeWalker recording_extension/capture/side-effect-observer.ts，P3.6）。
//
// 检测动作引发的 DOM 变化（modal/dropdown 打开），作为 distiller 写 quirks.md 的实时原料。
// 仅在动作后 1s 窗口内观察（markAction 打时间戳），避免页面自身非用户触发的 DOM 变化误报。
// 检测到新增的 modal/dropdown 节点 → 通过 sendSignal 回调发 DistillSignal，
// 由 background POST /signal 到后端，collector.attach_signal 附到最近 capture event。
//
// 与 TreeWalker 的差异：发的是 DistillSignal（distill 场景），不是 TreeWalker SignalEvent；
// collector 拿到后不参与重放定位，只作为 quirks 原料喂给 LLM。

import type { DistillSignal, SignalKind } from "../shared/distill-schema";

interface InstallOptions {
  /** 检测到副作用时回调（发 DistillSignal，由 content → background → POST /signal） */
  sendSignal: (signal: DistillSignal) => void;
}

export interface SideEffectHandle {
  uninstall: () => void;
  /** 动作发出时调用，打时间戳开启 1s 观察窗口。 */
  markAction: (ts: number) => void;
}

/** modal 容器选择器（覆盖常见组件库：Semi / Antd / 通用）。 */
const MODAL_SELECTOR =
  '[role="dialog"], [aria-modal="true"], .modal, .ant-modal, .semi-modal, .semi-sidesheet';
/** 下拉选择器。 */
const DROPDOWN_SELECTOR =
  '[role="listbox"], .ant-select-dropdown, .semi-select-option-list, .semi-dropdown, .semi-popover';

/** 仅在动作后这段时间内的 DOM 变化视为副作用（毫秒）。 */
const ACTION_WINDOW_MS = 1000;
/** 同一选择器去重窗（毫秒）——避免一个 modal 多批 mutation 重复发信号。 */
const DEDUPE_WINDOW_MS = 500;

/**
 * 装配副作用观察器。
 * 返回 handle：uninstall 卸载，markAction 在每个采集动作后调（开 1s 观察窗口）。
 * 重复 install 返回空 handle（幂等）。
 */
export function installSideEffectObserver(opts: InstallOptions): SideEffectHandle {
  const { sendSignal } = opts;

  let lastActionTs = 0;
  let lastEmitted = ""; // `${type}:${selector}` 去重

  const observer = new MutationObserver((mutations) => {
    // 只在动作后窗口内观察，抑制页面自身 DOM 变化误报
    if (lastActionTs === 0 || Date.now() - lastActionTs > ACTION_WINDOW_MS) return;

    for (const m of mutations) {
      for (const node of Array.from(m.addedNodes)) {
        if (!(node instanceof HTMLElement)) continue;
        // 新增节点本身是 modal/dropdown，或其内含 modal/dropdown（wrapper 套层）
        detectAndEmit(node, MODAL_SELECTOR, "modal_opened");
        detectAndEmit(node, DROPDOWN_SELECTOR, "dropdown_opened");
      }
    }
  });

  const detectAndEmit = (
    root: HTMLElement,
    selector: string,
    type: SignalKind,
  ): void => {
    const target = root.matches(selector) ? root : (root.querySelector<HTMLElement>(selector) ?? null);
    if (!target) return;
    const sel = selectorOf(target);
    const now = Date.now();
    const key = `${type}:${sel}`;
    // 同 type+selector 在去重窗内只发一次
    if (lastEmitted === key && now - lastActionTs < DEDUPE_WINDOW_MS) return;
    lastEmitted = key;
    sendSignal({ type, selector: sel, ts: now });
  };

  observer.observe(document, { childList: true, subtree: true });

  return {
    uninstall: () => {
      observer.disconnect();
    },
    markAction: (ts: number) => {
      lastActionTs = ts;
    },
  };
}

/** 简易选择器：tag + #id + 前两个 class（collector 只用 selector 作 signal.detail，不参与定位）。 */
function selectorOf(el: HTMLElement): string {
  const parts = [el.tagName.toLowerCase()];
  if (el.id) parts.push(`#${el.id}`);
  const cls =
    typeof el.className === "string" ? el.className.trim().split(/\s+/).filter(Boolean) : [];
  for (const c of cls.slice(0, 2)) parts.push(`.${c}`);
  return parts.join("");
}
