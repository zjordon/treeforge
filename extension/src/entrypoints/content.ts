/**
 * content script（ISOLATED world, allFrames）—— 采集编排层。
 *
 * 职责（借鉴 TreeWalker content.ts 架构，通用骨架）：
 *   - 尽早注入 injected.js 到 MAIN world（hook history + addEventListener，P3.6）
 *   - 监听 background 的状态广播，录制开始时装配 RecorderEngine，停止时卸载
 *   - RecorderEngine 采到事件后，包成 CaptureEnvelope 发给 background（→ POST /ingest）
 *   - 装配 navigation-recorder（收 tf:nav/popstate/hashchange → emit navigate）
 *   - 装配 side-effect-observer（modal/dropdown 副作用 → background → POST /signal）
 *
 * 通用骨架：不感知具体策略。策略在装配时注入（distill 场景用 DistillStrategy）。
 */

import { installNavigationRecorder } from "../capture/navigation-recorder";
import {
  installSideEffectObserver,
  type SideEffectHandle,
} from "../capture/side-effect-observer";
import { RecorderEngine } from "../core/recorder-engine";
import { DistillStrategy } from "../strategies/distill";
import type { CaptureEnvelope, CaptureScenario } from "../shared/envelope";
import type { DistillEventPayload, DistillSignal } from "../shared/distill-schema";
import type { ContentMessage, RecordingState, StateBroadcast } from "../shared/types";

export default defineContentScript({
  matches: ["http://*/*", "https://*/*"],
  allFrames: true,
  runAt: "document_idle",
  main() {
    // 尽早注入 MAIN-world 脚本（hook history.pushState/replaceState + addEventListener）。
    // 必须在录制开始前装好——动态组件可能在 startRecording 前就注册点击监听器。
    // 用 <script src> 注入到页面主世界（content 在 ISOLATED world，覆盖不到页面 history）。
    injectMainWorldScript();

    let engine: RecorderEngine | null = null;
    let navCleanup: (() => void) | null = null;
    let sideEffect: SideEffectHandle | null = null;
    let currentSessionId: string | null = null;
    let currentScenario: CaptureScenario = "distill";

    /** 把 payload 包成 envelope 发给 background */
    function emitToBackground(payload: DistillEventPayload): void {
      if (!currentSessionId) return;
      const envelope: CaptureEnvelope<DistillEventPayload> = {
        scenario: currentScenario,
        session_id: currentSessionId,
        ts: Date.now(),
        url: location.href,
        is_top_frame: window === window.top,
        payload,
      };
      console.info("[treeforge-content] emit", payload.type, "from", location.href.slice(0, 50));
      chrome.runtime.sendMessage({ type: "capture-event", envelope }).catch(() => {});
    }

    /** 把 signal 发给 background（→ POST /signal） */
    function emitSignalToBackground(signal: DistillSignal): void {
      if (!currentSessionId) return;
      console.info("[treeforge-content] signal", signal.type, "from", location.href.slice(0, 50));
      chrome.runtime
        .sendMessage({ type: "capture-signal", signal, sessionId: currentSessionId })
        .catch(() => {});
    }

    /** 装配 recorder + 导航监听 + 副作用观察（按场景选策略） */
    function startRecording(state: RecordingState): void {
      if (engine) return; // 已装配
      currentSessionId = state.sessionId;
      currentScenario = state.scenario;
      // P2.3.1 只支持 distill 策略；replay 策略待后续
      const strategy = state.scenario === "distill" ? new DistillStrategy() : new DistillStrategy();
      engine = new RecorderEngine(strategy, emitToBackground);

      // 副作用观察器：先装，再把 markAction 注入 engine（engine emit 后开 1s 窗口）
      sideEffect = installSideEffectObserver({ sendSignal: emitSignalToBackground });
      engine.setActionHook((ts) => sideEffect?.markAction(ts));

      engine.install();

      // 导航监听：tf:nav（MAIN-world hook）+ popstate/hashchange → emit navigate payload
      navCleanup = installNavigationRecorder({
        sendNavigate: (url) => emitToBackground({ type: "navigate", url }),
      });

      console.info(
        "[treeforge-content] recorder installed on",
        location.href,
        "session=",
        state.sessionId,
      );
    }

    /** 卸载 recorder + 导航监听 + 副作用观察 */
    function stopRecording(): void {
      navCleanup?.();
      navCleanup = null;
      sideEffect?.uninstall();
      sideEffect = null;
      if (!engine) return;
      engine.uninstall();
      engine = null;
      currentSessionId = null;
      console.info("[treeforge-content] recorder uninstalled on", location.href);
    }

    // 监听 background 的状态广播
    chrome.runtime.onMessage.addListener((msg: StateBroadcast | ContentMessage) => {
      // state 广播：控制 recorder 装配/卸载
      if (msg?.type === "state") {
        const stateMsg = msg as StateBroadcast;
        console.info(
          "[treeforge-content] received state broadcast:",
          stateMsg.state.recording ? "recording" : "idle",
          "on",
          location.href,
        );
        if (stateMsg.state.recording && stateMsg.state.sessionId) {
          startRecording(stateMsg.state);
        } else {
          stopRecording();
        }
      }
    });

    // content 启动时问 background 当前是否在录（处理页面刷新/SW 重连）
    // background 会回 RecordingState；若在录则装配 recorder
    chrome.runtime
      .sendMessage({ type: "recording-active-query" })
      .then((state: RecordingState | undefined) => {
        if (state?.recording && state?.sessionId) {
          startRecording(state);
        }
      })
      .catch(() => {});
  },
});

/**
 * 把 injected.js 注入到页面 MAIN world（hook history + addEventListener）。
 * 用 <script src>（web_accessible_resources 已声明 injected.js）注入：
 *   - src 方式加载的脚本运行在页面主世界（与页面共享 history/EventTarget）
 *   - 防重复：injected.ts 内有 __tfNavHooked / __tfAELHooked 守卫
 * 幂等：多次调用安全（WXT 在 build 时把 entrypoints/injected.ts 编译成 injected.js）。
 */
function injectMainWorldScript(): void {
  // 已注入过则跳过（页面可能多次执行 content script）
  if (document.getElementById("treeforge-injected")) return;
  const s = document.createElement("script");
  s.id = "treeforge-injected";
  // WXT 的 browser.runtime.getURL 类型签名限制成 PublicPath（已知入口资源），
  // injected.js 是 web_accessible_resources 里的动态资源，强制断言成 string 取 URL。
  s.src = browser.runtime.getURL("injected.js" as never);
  s.async = false; // 同步加载，确保 hook 在页面后续脚本前装好
  (document.head || document.documentElement).appendChild(s);
  // 加载后移除 script 标签（脚本已执行，hook 已装好，标签无用了）
  s.onload = () => s.remove();
  s.onerror = () => s.remove();
}

