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

/**
 * input 去抖时长（毫秒）—— 通用录制去噪值。
 * 1200ms 能合并大部分连续打字，又不至于误合并用户有意停顿后切换的操作
 * （onInput 的 target-switch flush 会先冲刷前一个）。主要去碎片化靠后端
 * atomizer 的「同目标连续 input 合并」（确定性兜底），这里只是预防性窗口。
 */
const INPUT_COALESCE_MS = 1200;

/** 可交互元素选择器（通用 a11y/交互元素，不含 data-tw-jsclick 私有标记）。
 * contenteditable 同时覆盖 ="true" 和 =""（空字符串也是可编辑，按 HTML spec）。 */
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
  "[contenteditable='true'],[contenteditable='']",
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
    // contenteditable 富文本编辑器（如 bilibili 简介）：补抓语义提示，
    // 避免点击渲染成「div contenteditable=true :: div」无法与其它编辑器区分。
    if (target instanceof HTMLElement && target.isContentEditable) {
      const label = contentEditableLabel(target);
      if (label) return label.slice(0, 60);
    }
    // 通用：可见文本 / aria-label / placeholder / tag
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

/**
 * 给 contenteditable 元素找一个人类可读的语义标签。
 *
 * 背景：富文本编辑器（bilibili 简介、富文本评论框）通常是裸 <div contenteditable>
 * 无 id/name/aria-label，点击后只能渲染成「div contenteditable=true」，LLM 无法
 * 区分页面上多个编辑器。这里按 WAI-ARIA 标准关联 + DOM 启发式补语义：
 *
 *   1. aria-labelledby 指向的元素文本（WAI-ARIA 标准关联，最可靠）
 *   2. 邻近的前序标题/标签元素（同可交互祖先内，查 h1-h6/label/[data-label]）
 *   3. 兜底返回 ""（调用方再退化到通用逻辑）
 *
 * 例：bilibili 简介 DOM 是 <h3>简介</h3> + <div contenteditable>，
 * 命中启发式 2，返回「简介」。
 */
function contentEditableLabel(el: HTMLElement): string {
  // 1. aria-labelledby：标准关联，id 列表空格分隔，取首个非空文本
  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    for (const id of labelledBy.trim().split(/\s+/)) {
      const ref = id ? document.getElementById(id) : null;
      const text = ref ? cleanVisibleText(ref) : "";
      if (text) return text;
    }
  }

  // 2. 邻近标题/标签：向上找最近的「区块容器」，在其内查前序标题元素。
  // 容器界定：向上最多 5 层，找到一个有多个子节点或 role=group/form 的祖先。
  let container: Element | null = el;
  for (let i = 0; i < 5 && container; i++) {
    const parent = container.parentElement;
    if (!parent) break;
    container = parent;
    // 容器内查标题/标签（限定在容器范围，避免命中页面其它无关标题）
    const heading = container.querySelector("h1,h2,h3,h4,h5,h6,label,[data-label]");
    if (heading) {
      const text = cleanVisibleText(heading);
      if (text) return text;
    }
  }
  return "";
}
