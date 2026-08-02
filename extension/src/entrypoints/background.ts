/**
 * background（MV3 service worker）—— 录制中枢。
 *
 * 职责（借鉴 TreeWalker background.ts 架构，通用骨架）：
 *   - 维护录制状态（chrome.storage）
 *   - 收 popup 的 start/stop 控制消息
 *   - 收 content 的采集事件，POST /ingest 到 Python 后端
 *   - 状态广播给 content（控制 recorder 装配/卸载）
 *
 * MV3 友好：用 fetch POST（非 WebSocket 长连接），SW 按需唤醒。
 */

import { postIngest, postSignal, postStart, postStop } from "../shared/backend";
import type { CaptureEnvelope } from "../shared/envelope";
import type { DistillSignal } from "../shared/distill-schema";
import {
  INITIAL_STATE,
  STORAGE_KEY,
  type ControlMessage,
  type RecordingState,
  type StateBroadcast,
} from "../shared/types";

export default defineBackground(() => {
  // ---- 状态管理 ----

  async function getState(): Promise<RecordingState> {
    const stored = await chrome.storage.local.get(STORAGE_KEY);
    return { ...INITIAL_STATE, ...(stored[STORAGE_KEY] as Partial<RecordingState> | undefined) };
  }

  async function setState(patch: Partial<RecordingState>): Promise<RecordingState> {
    const current = await getState();
    const next = { ...current, ...patch };
    await chrome.storage.local.set({ [STORAGE_KEY]: next });
    // 广播给所有 content（控制 recorder 装配/卸载）
    broadcastState(next);
    return next;
  }

  function broadcastState(state: RecordingState, onlyTabId?: number): void {
    const msg: StateBroadcast = { type: "state", state };
    if (onlyTabId !== undefined) {
      chrome.tabs.sendMessage(onlyTabId, msg).catch(() => {});
      return;
    }
    chrome.tabs.query({}, (tabs) => {
      for (const tab of tabs) {
        if (tab.id) chrome.tabs.sendMessage(tab.id, msg).catch(() => {});
      }
    });
  }

  // ---- 控制消息（来自 popup）----

  chrome.runtime.onMessage.addListener((msg: ControlMessage, _sender, sendResponse) => {
    (async () => {
      if (msg.type === "query-state") {
        sendResponse(await getState());
        return;
      }
      if (msg.type === "start-recording") {
        await handleStart(msg.scenario, msg.endpoint);
        sendResponse(await getState());
        return;
      }
      if (msg.type === "stop-recording") {
        await handleStop();
        sendResponse(await getState());
        return;
      }
      if (msg.type === "recording-active-query") {
        // content script 启动时询问是否在录（页面刷新/新开/SW 重连后恢复）
        const state = await getState();
        sendResponse(state);
        // 如果正在录，主动给这个 tab 补发一次 state（让 content 装配 recorder）
        if (state.recording && _sender.tab?.id) {
          broadcastState(state, _sender.tab.id);
        }
        return;
      }
    })();
    return true; // 异步响应
  });

  async function handleStart(scenario: "distill" | "replay", endpoint?: string): Promise<void> {
    const state = await getState();
    const targetEndpoint = endpoint || state.endpoint;
    try {
      const resp = await postStart(targetEndpoint, { scenario });
      if (!resp.ok || !resp.session_id) {
        console.error("[treeforge] start failed:", resp.error);
        return;
      }
      await setState({
        recording: true,
        scenario,
        sessionId: resp.session_id,
        endpoint: targetEndpoint,
        eventCount: 0,
      });
      console.info("[treeforge] recording started:", resp.session_id);
    } catch (e) {
      console.error("[treeforge] start error (is Python backend running?):", e);
    }
  }

  async function handleStop(): Promise<void> {
    const state = await getState();
    if (!state.recording) return;
    try {
      await postStop(state.endpoint);
    } catch (e) {
      console.error("[treeforge] stop error:", e);
    }
    await setState({ recording: false, sessionId: null, eventCount: 0 });
    console.info("[treeforge] recording stopped");
  }

  // ---- 采集事件 / 副作用信号（来自 content）----

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    // 采集事件（POST /ingest）
    if (msg?.type === "capture-event") {
      (async () => {
        const state = await getState();
        if (!state.recording) return; // 未录制忽略
        const envelope = msg.envelope as CaptureEnvelope;
        // 确保 envelope 有 session_id（content 可能没填）
        if (!envelope.session_id) envelope.session_id = state.sessionId || "";
        // 注入来源 tab id：后端据此精确 attach CDP target（解决多 tab 误连）。
        // content script 无法访问 chrome.tabs，只能由 background 从 sender.tab.id 取。
        if (sender.tab?.id) envelope.tab_id = sender.tab.id;
        try {
          await postIngest(state.endpoint, envelope);
          await setState({ eventCount: state.eventCount + 1 });
        } catch (e) {
          console.error("[treeforge] ingest error:", e);
        }
      })();
      sendResponse({ ok: true });
      return false; // 同步响应
    }

    // 副作用信号（POST /signal）—— P3.6 迁自 TreeWalker
    if (msg?.type === "capture-signal") {
      (async () => {
        const state = await getState();
        if (!state.recording) return; // 未录制忽略
        const { signal, sessionId } = msg as {
          signal: DistillSignal;
          sessionId: string;
        };
        try {
          await postSignal(state.endpoint, state.sessionId || sessionId, signal);
        } catch (e) {
          console.error("[treeforge] signal error:", e);
        }
      })();
      sendResponse({ ok: true });
      return false; // 同步响应
    }

    return false; // 非采集消息，不处理
  });

  // SW 启动时同步状态（MV3 SW 被唤醒后恢复）
  getState().then((state) => {
    if (state.recording) broadcastState(state);
  });
});
