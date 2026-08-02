// SPA 导航采集（迁自 TreeWalker recording_extension/capture/navigation-recorder.ts，P3.6）。
//
// 注入的 MAIN-world injected.ts hook 了 pushState/replaceState（发 tf:nav），
// 本模块在 ISOLATED world 直接监听 tf:nav + popstate/hashchange（content 能收到的标准事件）
// → 统一通过 emit 回调发 navigate(url) payload。
//
// lastUrl 去重：pushState hook 与 popstate/hashchange 不会对同一 URL 重复发。
//
// 与 TreeWalker 的差异：emit 的是 DistillEventPayload（distill 场景），不是 RecorderEvent。
// go_back 折叠：popstate 无法可靠区分「后退按钮」与「SPA 回退」，统一记 navigate(url)
// （distill 只要 LLM 知道用户去了哪个 URL，不关心是后退还是跳转）。

interface InstallOptions {
  /** 收到导航时回调（发 DistillEventPayload.navigate） */
  sendNavigate: (url: string) => void;
}

/**
 * 装配导航监听，返回 cleanup 函数。
 * sendNavigate 在 URL 变化时被调用（已去重）。
 */
export function installNavigationRecorder(opts: InstallOptions): () => void {
  const { sendNavigate } = opts;

  let lastUrl = location.href;
  const onNav = (): void => {
    const url = location.href;
    if (url === lastUrl) return; // 去重（hook 与 popstate 可能都触发）
    lastUrl = url;
    sendNavigate(url);
  };

  // tf:nav：MAIN-world injected.ts 的 pushState/replaceState hook 派发（content 能收到，共享 window）
  window.addEventListener("tf:nav", onNav);
  // popstate/hashchange：content 直接监听（后退/前进/锚点变化）
  window.addEventListener("popstate", onNav);
  window.addEventListener("hashchange", onNav);

  return () => {
    window.removeEventListener("tf:nav", onNav);
    window.removeEventListener("popstate", onNav);
    window.removeEventListener("hashchange", onNav);
  };
}
