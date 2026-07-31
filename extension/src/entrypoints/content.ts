/**
 * content script（ISOLATED world, allFrames）—— 采集编排层。
 *
 * 职责（借鉴 TreeWalker content.ts 架构，通用骨架）：
 *   - 监听 background 的状态广播，录制开始时装配 RecorderEngine，停止时卸载
 *   - RecorderEngine 采到事件后，包成 CaptureEnvelope 发给 background（→ POST /ingest）
 *
 * 通用骨架：不感知具体策略。策略在装配时注入（distill 场景用 DistillStrategy）。
 */

import { RecorderEngine } from "../core/recorder-engine";
import { DistillStrategy } from "../strategies/distill";
import type { CaptureEnvelope, CaptureScenario } from "../shared/envelope";
import type { DistillEventPayload } from "../shared/distill-schema";
import type { RecordingState, StateBroadcast } from "../shared/types";

export default defineContentScript({
  matches: ["<all_urls>"],
  allFrames: true,
  runAt: "document_idle",
  main() {
    let engine: RecorderEngine | null = null;
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
      chrome.runtime.sendMessage({ type: "capture-event", envelope }).catch(() => {});
    }

    /** 装配 recorder（按场景选策略） */
    function startRecording(state: RecordingState): void {
      if (engine) return; // 已装配
      currentSessionId = state.sessionId;
      currentScenario = state.scenario;
      // P2.3.1 只支持 distill 策略；replay 策略待 TreeWalker 迁入
      const strategy = state.scenario === "distill" ? new DistillStrategy() : new DistillStrategy();
      engine = new RecorderEngine(strategy, emitToBackground);
      engine.install();
    }

    /** 卸载 recorder */
    function stopRecording(): void {
      if (!engine) return;
      engine.uninstall();
      engine = null;
      currentSessionId = null;
    }

    // 监听 background 的状态广播
    chrome.runtime.onMessage.addListener((msg: StateBroadcast) => {
      if (msg?.type !== "state") return;
      if (msg.state.recording && msg.state.sessionId) {
        startRecording(msg.state);
      } else {
        stopRecording();
      }
    });

    // content 启动时问 background 当前是否在录（处理页面刷新/SW 重连）
    chrome.runtime.sendMessage({ type: "recording-active-query" }).catch(() => {});
  },
});
