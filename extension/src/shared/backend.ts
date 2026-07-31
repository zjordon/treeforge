/**
 * HTTP 传输层：扩展 → Python 采集后端（treeforge/capture/backend.py）。
 *
 * 4 个端点（对齐 backend.py）：
 *   POST /start   { scenario, config }     → { ok, session_id }
 *   POST /ingest  { CaptureEnvelope }       → { ok }
 *   POST /stop    {}                         → { ok, result }
 *   GET  /health                              → { ok: true }
 *
 * 从 TreeWalker recording_extension/shared/backend.ts 泛化（端点参数化）。
 */

import { DEFAULT_ENDPOINT, type CaptureEnvelope, type CaptureScenario } from "./envelope";

/** Start 请求体 */
export interface StartRequest {
  scenario: CaptureScenario;
  config?: Record<string, unknown>;
}

/** Start 响应 */
export interface StartResponse {
  ok: boolean;
  session_id?: string;
  error?: string;
}

/** 通用响应 */
export interface BackendResponse {
  ok: boolean;
  error?: string;
  result?: unknown;
  [key: string]: unknown;
}

/** 检查后端健康（GET /health） */
export async function checkHealth(endpoint: string = DEFAULT_ENDPOINT): Promise<boolean> {
  try {
    const resp = await fetch(`${endpoint}/health`);
    return resp.ok;
  } catch {
    return false;
  }
}

/** 开始采集（POST /start） */
export async function postStart(
  endpoint: string,
  body: StartRequest,
): Promise<StartResponse> {
  const resp = await fetch(`${endpoint}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return resp.json();
}

/** 提交采集信封（POST /ingest） */
export async function postIngest(
  endpoint: string,
  envelope: CaptureEnvelope,
): Promise<BackendResponse> {
  const resp = await fetch(`${endpoint}/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(envelope),
  });
  return resp.json();
}

/** 停止采集（POST /stop） */
export async function postStop(endpoint: string): Promise<BackendResponse> {
  const resp = await fetch(`${endpoint}/stop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  return resp.json();
}
