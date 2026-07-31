/**
 * 扩展内部消息类型（background ↔ content ↔ popup 通信）。
 *
 * MV3 架构：popup/content 通过 chrome.runtime.sendMessage 发给 background，
 * background 是唯一与 Python 后端通信的入口（对齐 TreeWalker 架构）。
 */

/** 录制状态（存 chrome.storage，popup/background 共享） */
export interface RecordingState {
  recording: boolean;
  scenario: "distill" | "replay";
  sessionId: string | null;
  /** Python 后端地址（默认 DEFAULT_ENDPOINT） */
  endpoint: string;
  /** 已采集事件数（展示用） */
  eventCount: number;
}

/** popup → background 的控制消息 */
export type ControlMessage =
  | { type: "start-recording"; scenario: "distill" | "replay"; endpoint?: string }
  | { type: "stop-recording" }
  | { type: "query-state" };

/** background → popup/content 的状态广播 */
export type StateBroadcast = { type: "state"; state: RecordingState };

/** content → background 的采集事件（content 采到事件后转发） */
export type ContentMessage =
  | { type: "capture-event"; envelope: import("./envelope").CaptureEnvelope }
  | { type: "recording-active-query" }; // content 启动时问 background 是否在录

/** chrome.storage 的 key */
export const STORAGE_KEY = "tf_recording_state";

/** 初始状态 */
export const INITIAL_STATE: RecordingState = {
  recording: false,
  scenario: "distill",
  sessionId: null,
  endpoint: "http://127.0.0.1:8765",
  eventCount: 0,
};
