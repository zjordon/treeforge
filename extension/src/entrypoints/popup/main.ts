/**
 * popup 控制逻辑——开始/停止录制，显示状态。
 *
 * 纯 JS（不引 React，P2.3.1 减依赖）。
 * 通过 chrome.runtime.sendMessage 与 background 通信。
 */

import type { ControlMessage, RecordingState } from "../../shared/types";

const statusEl = document.getElementById("status") as HTMLDivElement;
const toggleBtn = document.getElementById("toggle") as HTMLButtonElement;
const endpointInput = document.getElementById("endpoint") as HTMLInputElement;
const scenarioSelect = document.getElementById("scenario") as HTMLSelectElement;
const eventCountEl = document.getElementById("eventCount") as HTMLSpanElement;
const hintEl = document.getElementById("hint") as HTMLDivElement;

function send(msg: ControlMessage): Promise<RecordingState> {
  return chrome.runtime.sendMessage(msg);
}

function renderState(state: RecordingState): void {
  if (state.recording) {
    statusEl.textContent = `录制中（${state.scenario}）`;
    statusEl.className = "status recording";
    toggleBtn.textContent = "停止录制";
    toggleBtn.className = "stop";
    endpointInput.disabled = true;
    scenarioSelect.disabled = true;
  } else {
    statusEl.textContent = "未录制";
    statusEl.className = "status idle";
    toggleBtn.textContent = "开始录制";
    toggleBtn.className = "start";
    endpointInput.disabled = false;
    scenarioSelect.disabled = false;
  }
  eventCountEl.textContent = String(state.eventCount || 0);
  endpointInput.value = state.endpoint || "http://127.0.0.1:8765";
  scenarioSelect.value = state.scenario || "distill";
}

// 初始加载状态
send({ type: "query-state" }).then(renderState).catch(() => {
  hintEl.textContent = "（无法连接 background）";
});

// 开始/停止
toggleBtn.addEventListener("click", async () => {
  toggleBtn.disabled = true;
  const state = await send({ type: "query-state" });
  if (state.recording) {
    const next = await send({ type: "stop-recording" });
    renderState(next);
  } else {
    const next = await send({
      type: "start-recording",
      scenario: scenarioSelect.value as "distill" | "replay",
      endpoint: endpointInput.value,
    });
    renderState(next);
    if (!next.recording) {
      hintEl.textContent = "启动失败——确认 Python 后端在跑（uv run treeforge capture）";
      setTimeout(() => (hintEl.textContent = ""), 4000);
    } else {
      hintEl.textContent = "";
    }
  }
  toggleBtn.disabled = false;
});

// 定时刷新事件数（录制中）
setInterval(async () => {
  const state = await send({ type: "query-state" });
  if (state.recording) {
    eventCountEl.textContent = String(state.eventCount || 0);
  }
}, 1000);
