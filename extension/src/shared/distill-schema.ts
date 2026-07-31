/**
 * distill 场景的 payload schema —— Python 侧 treeforge/capture/distill_schema.py 的 TS 镜像。
 *
 * 双端契约：扩展按此产出 payload，Python collector 按 distill_schema.py 消费。
 * 字段名/类型/语义必须严格对齐（改一边必须同步改另一边）。
 *
 * 【核心设计】扩展采「DOM 原始属性 raw_attrs」，Python collector 提炼成 element_attrs。
 * 理由：白名单是蒸馏业务逻辑，不该焊进通用扩展（详见 Python distill_schema.py 模块 docstring）。
 */

/** distill 动作类型（通用浏览器动作，非 TreeWalker action 注册表镜像） */
export type DistillActionType =
  | "click" // click 事件
  | "input" // input 事件（文本输入）
  | "change" // change 事件（select/checkbox/file）
  | "keydown" // keydown 事件（Enter/快捷键）
  | "scroll" // wheel 事件（已去抖合并）
  | "navigate"; // SPA 导航或整页跳转

/**
 * 扩展采集的原始元素属性键（对齐 Python RAW_ATTR_KEYS）。
 * content script 从 DOM 元素读这些属性，塞进 raw_attrs。
 */
export const RAW_ATTR_KEYS = [
  // 通用身份
  "tag", // tagName.toLowerCase()
  "id",
  "name",
  // 类型/状态
  "type", // input 的 type
  "role",
  "aria-label",
  "ariaLabel", // 兜底：aria-label 取不到时用可访问名计算
  // 表单提示
  "placeholder",
  // 测试标识（蒸馏「稳定标识」关键来源）
  "data-testid",
  "data-test",
  "data-cy",
  // 富文本
  "contenteditable",
  // 文件上传（file input 专属，对 quirks 关键）
  "accept",
] as const;

/** distill 事件 payload（CaptureEnvelope.payload 的 distill 场景形状） */
export interface DistillEventPayload {
  /** 动作类型（必填） */
  type: DistillActionType;
  /** 操作目标元素的原始属性（collector 过滤成 element_attrs）。可能为空（navigate 无目标） */
  raw_attrs?: Record<string, string>;
  /** 输入值（input/change 用） */
  value?: string;
  /** 按键（keydown 用），如 "Enter" */
  key?: string;
  /** 滚动量（scroll 用） */
  scroll_amount?: number;
  /** 导航目标 URL（navigate 用） */
  url?: string;
  /** 目标元素可读标签（扩展算的人类可读描述，作为 target 字段），可选 */
  target?: string;
  /** 时间戳（毫秒），由 envelope.ts 填，这里冗余便于单独传递 */
  ts?: number;
}

/**
 * 从 DOM 元素提取 raw_attrs（对齐 Python extract_element_attrs 的输入）。
 * 扩展 content script 用此函数采属性，Python collector 再提炼白名单。
 */
export function extractRawAttrs(el: Element): Record<string, string> {
  const attrs: Record<string, string> = {};
  const tag = el.tagName.toLowerCase();
  if (tag) attrs.tag = tag;

  for (const key of RAW_ATTR_KEYS) {
    if (key === "tag" || key === "ariaLabel") continue; // tag 已加；ariaLabel 单独处理
    const v = el.getAttribute(key);
    if (v !== null && v !== "") {
      attrs[key] = v;
    }
  }

  // aria-label 兜底：getAttribute 取不到时，用元素的可访问名（computedName）
  if (!attrs["aria-label"]) {
    // 简单兜底：用 ariaLabel 属性（部分浏览器暴露）或 title
    const aria = (el as HTMLElement).ariaLabel || el.getAttribute("title") || "";
    if (aria) {
      // 注意：放 ariaLabel key（与 Python RAW_ATTR_KEYS 的 ariaLabel 对齐，collector 兜底用）
      attrs.ariaLabel = aria;
    }
  }

  // visible_text（计算的，非 getAttribute）：清洗字体图标后的人类可读文本
  const visibleText = cleanVisibleText(el);
  if (visibleText) {
    attrs.visible_text = visibleText;
  }

  return attrs;
}

/** 私有 Unicode 区（字体图标），清洗 visible_text 用（对齐 Python _PRIVATE_UNICODE_RANGES） */
const PRIVATE_UNICODE_RANGES: ReadonlyArray<readonly [number, number]> = [
  [0xe000, 0xf8ff], // BMP Private Use Area
  [0xf0000, 0xffffd], // Supplementary PUA-A
  [0x100000, 0x10fffd], // Supplementary PUA-B
];

/**
 * 清洗可见文本：去除私有 Unicode 区字符（字体图标）+ 压缩空白。
 * 对齐 Python clean_visible_text。
 */
export function cleanVisibleText(elOrText: Element | string | null | undefined): string {
  let raw: string;
  if (typeof elOrText === "string") {
    raw = elOrText;
  } else if (elOrText instanceof Element) {
    // contenteditable 用 innerText（含可见格式），其它用 textContent
    raw = elOrText.textContent || "";
  } else {
    return "";
  }

  if (!raw) return "";

  // 去私有 Unicode 区字符
  let cleaned = "";
  for (const ch of raw) {
    const cp = ch.codePointAt(0)!;
    if (PRIVATE_UNICODE_RANGES.some(([lo, hi]) => cp >= lo && cp <= hi)) continue;
    cleaned += ch;
  }
  // 压缩连续空白
  return cleaned.trim().split(/\s+/).join(" ");
}
