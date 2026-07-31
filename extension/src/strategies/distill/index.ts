/**
 * distill 采集策略——面向蒸馏场景的实现。
 *
 * 与 TreeWalker replay 策略的关键差异（docs/p2/README.md 3.2.5）：
 *   - click 不跳过误操作（蒸馏想看用户真实路径，含误点）
 *   - 字段采 raw_attrs（extractRawAttrs），不采 xpath/rect/upload_ctx（重放专属）
 *   - 不需要 data-tw-jsclick 标记
 *
 * P2.3.1 最小版：click/input/keydown(Enter)/scroll 各实现 buildXxxPayload。
 */

import type { CollectionStrategy } from "../../core/strategy";
import { cleanVisibleText, extractRawAttrs, type DistillEventPayload } from "../../shared/distill-schema";

/** input 去抖时长（毫秒）—— 通用录制去噪值 */
const INPUT_COALESCE_MS = 400;

/** 可交互元素选择器（通用 a11y/交互元素，不含 data-tw-jsclick 私有标记） */
const INTERACTIVE_SELECTOR = [
  "a[href]",
  "button",
  "input",
  "select",
  "textarea",
  "[role='button']",
  "[role='link']",
  "[role='checkbox']",
  "[role='radio']",
  "[role='tab']",
  "[contenteditable='true']",
  "[tabindex]",
].join(",");

/** distill 策略实现 */
export class DistillStrategy implements CollectionStrategy {
  readonly scenario = "distill" as const;
  readonly inputCoalesceMs = INPUT_COALESCE_MS;

  resolveClickTarget(rawTarget: Element): Element | null {
    // distill 策略：宽松——优先找可交互祖先，找不到也保留（蒸馏想看真实点击含误点）。
    // 与 replay 不同：replay 会跳过「重放定位不到」的点击，distill 保留。
    const ancestor = findInteractiveAncestor(rawTarget);
    return ancestor || rawTarget; // 找不到祖先就用原目标（保留误操作）
  }

  readInputValue(target: Element): string {
    // input/textarea 用 value，contenteditable 用 innerText
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) {
      return target.value;
    }
    if (target instanceof HTMLElement && target.isContentEditable) {
      return target.innerText;
    }
    return "";
  }

  extractAttrs(target: Element): Record<string, string> {
    return extractRawAttrs(target);
  }

  computeTargetLabel(target: Element): string {
    // 简单的人类可读标签：可见文本 / aria-label / placeholder / tag
    const text = cleanVisibleText(target);
    if (text) return text.slice(0, 60); // 截断长文本
    const aria = target.getAttribute("aria-label");
    if (aria) return aria;
    const placeholder = target.getAttribute("placeholder");
    if (placeholder) return placeholder;
    return target.tagName.toLowerCase();
  }

  buildClickPayload(target: Element): DistillEventPayload {
    return {
      type: "click",
      raw_attrs: this.extractAttrs(target),
      target: this.computeTargetLabel(target),
    };
  }

  buildInputPayload(target: Element, value: string): DistillEventPayload {
    return {
      type: "input",
      raw_attrs: this.extractAttrs(target),
      value,
      target: this.computeTargetLabel(target),
    };
  }

  buildKeydownPayload(key: string, target: Element | null): DistillEventPayload | null {
    // 只采 Enter（标签确认等关键操作）；Escape/Tab 暂不采（避免噪声）
    if (key !== "Enter") return null;
    return {
      type: "keydown",
      key,
      raw_attrs: target ? this.extractAttrs(target) : undefined,
    };
  }

  buildScrollPayload(amount: number): DistillEventPayload {
    return { type: "scroll", scroll_amount: amount };
  }

  buildNavigatePayload(url: string): DistillEventPayload {
    return { type: "navigate", url };
  }
}

/**
 * 找可交互祖先（通用 DOM 启发式，借鉴 TreeWalker findInteractiveAncestor 前三道）。
 *
 * 三道启发式（不含 TreeWalker 的第四道 data-tw-jsclick 私有标记）：
 *   1. 元素匹配 INTERACTIVE_SELECTOR
 *   2. DIV 且 cursor:pointer（排除内联元素）
 *   3. onclick/onmousedown 属性
 */
function findInteractiveAncestor(el: Element): Element | null {
  let node: Element | null = el;
  // 向上找最多 10 层（防止死循环 + 控制开销）
  for (let i = 0; i < 10 && node; i++) {
    if (node.matches(INTERACTIVE_SELECTOR)) return node;
    // DIV + cursor:pointer（仅 div，排除内联元素）
    if (
      node.tagName === "DIV" &&
      (node as HTMLElement).style?.cursor === "pointer"
    ) {
      return node;
    }
    // onclick/onmousedown 属性
    if (node.hasAttribute("onclick") || node.hasAttribute("onmousedown")) {
      return node;
    }
    node = node.parentElement;
  }
  return null;
}
