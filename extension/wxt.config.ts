import { defineConfig } from "wxt";

// TreeForge capture layer MV3 config（P2.3 实现）。
// 扩展采 DOM 事件 → POST 到 Python 采集后端（treeforge/capture/backend.py）。
// DOM 快照由 Python 后端经 remote-debugging-port CDP 负责，扩展不含 'debugger' 权限。
export default defineConfig({
  manifest: {
    name: "treeforge-capture",
    description: "TreeForge capture — record browser demos for distillation",
    version: "0.1.0",
    // storage: 录制状态；activeTab/scripting: content script 注入；
    // tabs: background 状态广播给所有 tab 的 content
    permissions: ["storage", "activeTab", "scripting", "tabs"],
    // host_permissions: 访问任意页面（采集）+ Python 后端
    // 注意：localhost 和 127.0.0.1 在 MV3 host_permissions 里不是一回事，都要声明
    // （否则扩展 fetch 到未声明的地址会被拦截，导致 /start 静默失败）
    host_permissions: [
      "http://localhost:8765/*",
      "http://127.0.0.1:8765/*",
      "http://*/*",
      "https://*/*",
    ],
  },
  srcDir: "src",
  outDir: ".output",
});
