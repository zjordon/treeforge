# TreeForge 扩展（采集层）

> **P2 待实现**。本期 P0 只搭了 WXT 脚手架结构，不实现功能。

## 角色参照 Browser-BC 的 MV3 采集层

人走一遍——这个扩展录制人类在浏览器里的示教操作（DOM 事件 / 选择器 / 网络调用 / 表单），
产出 trace JSON 喂给 `harness/` 蒸馏层。

详见知识库 `browserbc-extension-capture-layer.md`。

## 技术栈（init-plan §三）

- **WXT + React + TypeScript** —— MV3 最佳实践
- **pnpm** —— WXT 推荐
- **Dexie (IndexedDB)** —— MV3 SW 崩溃恢复靠 DB

## P2 计划

| 模块 | 职责 |
|---|---|
| `src/background/` | SW + recorder 状态机（30s 回收恢复） |
| `src/content/` | 14 种 DOM 事件采集 / DOM 快照 / 表单摘要 / selector fallback |
| `src/injected/` | monkey-patch fetch/XHR/WebSocket/history.pushState |
| `src/popup/` | React 录制控制 UI |

## 本期 P0 状态

- ✅ WXT 项目结构（`package.json` / `tsconfig.json` / `wxt.config.ts`）
- ✅ manifest name = `treeforge-capture`，permissions = `storage/activeTab/scripting`
- ✅ `src/{background,content,injected,popup}/` 目录 + `.gitkeep`
- ⏳ 真实采集逻辑（P2）

## 开发（P2 就绪后）

```bash
cd extension
pnpm install
pnpm dev          # Chrome 开发模式
pnpm dev:firefox  # Firefox
pnpm build        # 生产构建
```
