/**
 * distill 采集策略——面向蒸馏场景的实现。
 *
 * 与 TreeWalker replay 策略的关键差异（docs/p2/README.md 3.2.5）：
 *   - click 不跳过误操作（蒸馏想看用户真实路径，含误点）
 *   - 字段采 raw_attrs（extractRawAttrs），不采 xpath/rect（重放专属定位字段）
 *
 * P3.6 扩词（迁自 TreeWalker 扩展）：在保持 distill「LLM 可读」精度约束下，
 * 补 TreeForge 缺的事件词汇：select_dropdown / upload_file / send_keys。
 *   - upload_file 采 upload_ctx（站点无关语义身份），让 distiller 写进 quirks.md。
 *     与 TreeWalker 的差异：distill 不采 trigger_affordance（重放专用语义），
 *     不采 rect（定位字段，distill 不要）。
 *   - data-tw-jsclick 标记（MAIN-world addEventListener hook 打的）也纳入：distill
 *     要让 LLM 看到「这个 div 虽然 cursor:pointer 都没有，但 JS 监听了点击」。
 */

import type { CollectionStrategy } from "../../core/strategy";
import {
  cleanVisibleText,
  extractRawAttrs,
  type DistillEventPayload,
  type UploadCtx,
} from "../../shared/distill-schema";

/**
 * input 去抖时长（毫秒）—— 对齐 TreeWalker 通用录制去噪值。
 * P3.6 从 1200ms 调到 400ms（与 TreeWalker 一致）：final value only，
 * 主要去碎片化靠后端 atomizer 的「同目标连续 input 合并」（确定性兜底）。
 */
const INPUT_COALESCE_MS = 400;

/** 可交互元素选择器（通用 a11y/交互元素）。
 * data-tw-jsclick 标记不在选择器里，由 findInteractiveAncestor 单独查（迁自 TreeWalker）。
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

  buildScrollPayload(amount: number): DistillEventPayload {
    return { type: "scroll", scroll_amount: amount };
  }

  buildNavigatePayload(url: string): DistillEventPayload {
    return { type: "navigate", url };
  }

  // ---- P3.6 扩词：select / upload / send_keys（迁自 TreeWalker）----

  buildSelectPayload(target: Element, value: string): DistillEventPayload | null {
    return {
      type: "select_dropdown",
      raw_attrs: this.extractAttrs(target),
      value,
      target: this.computeTargetLabel(target),
    };
  }

  buildUploadPayload(input: HTMLInputElement, fileName: string): DistillEventPayload {
    // upload_ctx（站点无关语义身份，迁自 TreeWalker issue #139）：让 distiller 在 quirks.md
    // 描述「这个上传框是什么」，不依赖站点特定 selector。
    // 与 TreeWalker 的差异：不采 trigger_affordance（重放专用，distill 不要）。
    const uploadCtx = captureUploadCtx(input);
    const target = this.computeTargetLabel(input);
    return {
      type: "upload_file",
      raw_attrs: this.extractAttrs(input),
      value: fileName,
      target,
      upload_ctx: uploadCtx,
    };
  }

  buildSendKeysPayload(key: string, target: Element | null): DistillEventPayload | null {
    // distill 保留所有 send_keys（含纯命名键如 Enter）——LLM 看 SOP 要知道用户按了什么键。
    // 与 keydown 的区别：keydown 只采 Enter（P2.3.1），send_keys 覆盖修饰键组合 + 全部命名键。
    return {
      type: "send_keys",
      key,
      raw_attrs: target ? this.extractAttrs(target) : undefined,
    };
  }
}

/**
 * 找可交互祖先（通用 DOM 启发式，对齐 TreeWalker findInteractiveAncestor 四道）。
 *
 * 四道启发式（P3.6 补第四道 data-tw-jsclick 标记，补 content script 看不到 JS 监听器的盲区）：
 *   1. 元素匹配 INTERACTIVE_SELECTOR
 *   2. DIV 且 cursor:pointer（排除内联元素——span/svg 常继承父按钮的 cursor:pointer）
 *   3. onclick/onmousedown 属性
 *   4. data-tw-jsclick 标记（MAIN-world addEventListener hook 打的，对齐 TreeWalker）
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
    // data-tw-jsclick 标记（MAIN-world hook 打的，补 content script 看不到 addEventListener 的盲区）
    if (node.hasAttribute("data-tw-jsclick")) {
      return node;
    }
    node = node.parentElement;
  }
  return null;
}

/**
 * 取 upload file input 的通用身份线索（站点无关，迁自 TreeWalker issue #139 通用化）。
 *
 * 让 distiller 在 quirks.md 描述「这个上传框是什么」，跨框架/站点稳定
 * （Ant ant-upload / 原生 <label for> / Element el-upload 都覆盖）。
 *
 * 与 TreeWalker 的差异：不附 trigger_affordance（重放专用语义，distill 不要）。
 */
function captureUploadCtx(input: Element): UploadCtx {
  const norm = (s: string | null): string => (s ?? "").replace(/\s+/g, " ").trim();
  const htmlInput = input as HTMLInputElement;

  // 1. 原生 label 关联（W3C）：input.labels 同时含 <label for> 指向与包裹 <label>
  const labelText = norm(
    Array.from(htmlInput.labels ?? [])
      .map((l) => l.textContent ?? "")
      .join(" "),
  );

  // 2. aria-labelledby → 目标元素 textContent（IDREF 解析）
  const ariaText = norm(
    (input.getAttribute("aria-labelledby") ?? "")
      .split(/\s+/)
      .filter(Boolean)
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => !!el)
      .map((el) => el.textContent ?? "")
      .join(" "),
  );

  // 3. 就近可见文本祖先（≤5 层，首个 textContent 非空且 <200 字）
  let region = "";
  let p: Element | null = input.parentElement;
  let depth = 0;
  while (p && depth < 5 && !region) {
    const t = norm(p.textContent ?? "");
    if (t && t.length < 200) region = t;
    p = p.parentElement;
    depth += 1;
  }

  // 4. ARIA dialog（[role=dialog] / [aria-modal=true]）
  const inDialog = !!(input.closest('[role="dialog"]') ?? input.closest('[aria-modal="true"]'));

  return { label_text: labelText, aria_text: ariaText, region_text: region, in_dialog: inDialog };
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
    const parent: Element | null = container.parentElement;
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
