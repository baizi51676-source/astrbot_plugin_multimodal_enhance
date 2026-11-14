/**
 * AstrBot 插件页面 bridge 的类型定义。
 *
 * 页面运行在 AstrBot 的受限 iframe 内，所有请求都要经过宿主注入的
 * `window.AstrBotPluginPage`；这里只声明本插件实际用到的方法。
 */

export interface AppStateFeatures {
  image: boolean;
  audio: boolean;
  video: boolean;
  notice: boolean;
}

export interface AppStateBot {
  name: string;
  targets: string[];
  features: AppStateFeatures;
  overrides: Record<string, unknown>;
}

export interface AppState {
  enabled: boolean;
  features: AppStateFeatures;
  bots: AppStateBot[];
  caption_patch_installed: boolean;
  caption_provider: string;
  stt_configured: boolean;
  librosa: boolean;
  pipeline: { active_tasks: number; pending_results: number };
  version: string;
  recent_errors: { time: string; msg: string }[];
}

export type FieldType =
  | 'bool'
  | 'int'
  | 'str'
  | 'secret'
  | 'select'
  | 'provider'
  | 'textarea'
  | 'json';

export interface ConfigMetaItem {
  key: string;
  group: string;
  label: string;
  type: FieldType;
  hint?: string;
  scope?: string;
  options?: { value: string; label: string }[];
}

export interface BotRule {
  name: string;
  targets: string[];
  overrides: Record<string, unknown>;
}

export interface ConfigPayload {
  meta: ConfigMetaItem[];
  config: Record<string, unknown>;
  secrets_set: Record<string, boolean>;
  bots: BotRule[];
}

export interface ProviderOption {
  id: string;
  label: string;
}

export interface LogEntry {
  time: string;
  level: string;
  msg: string;
}

export interface EnvState {
  ffmpeg: { ok: boolean; path: string; version: string };
  ffprobe: { ok: boolean; path: string };
  ytdlp: { ok: boolean; version: string };
  modules: Record<string, boolean>;
  disk: { free?: number; total?: number; free_human?: string; total_human?: string };
  workdir: string;
  install: InstallState;
}

export interface InstallState {
  running: boolean;
  started_at: string | null;
  packages: string[];
  output: string;
  ok: boolean | null;
}

export interface SSEMessage {
  parsed?: unknown;
}

export interface Bridge {
  apiGet(path: string, query?: Record<string, unknown>): Promise<any>;
  apiPost(path: string, body?: unknown): Promise<any>;
  download(path: string, query?: Record<string, unknown>, filename?: string): Promise<void>;
  subscribeSSE(
    path: string,
    handlers: { onMessage(ev: SSEMessage): void; onError?(): void },
    query?: Record<string, unknown>,
  ): Promise<string>;
  unsubscribeSSE(id: string): Promise<void>;
  ready(): Promise<{ pageTitle?: string } | undefined>;
}

declare global {
  interface Window {
    AstrBotPluginPage?: Bridge;
  }
}

/**
 * 取宿主 bridge。
 *
 * 用函数而不是模块级常量：iframe 里宿主脚本与页面脚本的加载顺序由 AstrBot 决定，
 * 若页面脚本先执行，模块级常量会永久固化成 undefined。函数每次重新读取。
 */
export function getBridge(): Bridge | undefined {
  return window.AstrBotPluginPage;
}

/**
 * 惰性 bridge 视图。
 *
 * 各页面直接写 `bridge.apiGet(...)` 即可：读取时实时访问 window，
 * 既能兼容脚本加载顺序，也避免了每个调用点都写 getBridge()。
 */
export const bridge: Bridge = new Proxy({} as Bridge, {
  get(_target, prop) {
    const api = window.AstrBotPluginPage;
    if (!api) return undefined;
    const value = (api as unknown as Record<string | symbol, unknown>)[prop];
    return typeof value === 'function' ? value.bind(api) : value;
  },
});

/** 宿主是否就绪（渲染「请从 AstrBot 打开」提示时用）。 */
export function hasBridge(): boolean {
  return !!window.AstrBotPluginPage;
}
