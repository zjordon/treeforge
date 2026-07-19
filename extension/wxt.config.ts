import { defineConfig } from "wxt";

// TreeForge capture layer MV3 config.
// P2 才实现真实采集（init-plan §7.7）。本期只搭骨架：name/permissions 最小集，
// src/{background,content,injected,popup} 各放 .gitkeep。
export default defineConfig({
  // 扩展 manifest 名（init-plan §7.7）
  manifest: {
    name: "treeforge-capture",
    description: "TreeForge capture — record browser demos for distillation",
    // 最小权限集（init-plan §7.7：storage/activeTab/scripting）
    permissions: ["storage", "activeTab", "scripting"],
    host_permissions: ["<all_urls>"],
  },
  // React + TypeScript 模板（init-plan §三）
  srcDir: "src",
  outDir: ".output",
});
