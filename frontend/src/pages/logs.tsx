// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, Loader2, Radio, RefreshCw } from 'lucide-react';
import { bridge, hasBridge, type LogEntry } from '@/lib/bridge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { useToast } from '@/components/ui/toast';
import { cn } from '@/lib/utils';

const LEVEL_TONE: Record<string, string> = {
  ERROR: 'text-rose-400',
  WARN: 'text-amber-300',
  WARNING: 'text-amber-300',
  INFO: 'text-sky-300',
  DEBUG: 'text-zinc-400',
};

export default function LogsPage() {
  const toast = useToast();
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [streaming, setStreaming] = useState(false);
  const subIdRef = useRef<string | null>(null);
  const viewRef = useRef<HTMLPreElement | null>(null);

  const scrollToBottom = () => {
    const el = viewRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  };

  const load = useCallback(async () => {
    if (!hasBridge()) return;
    setLoading(true);
    try {
      const data = (await bridge.apiGet('logs', { n: 400 })) as { logs?: LogEntry[] };
      setEntries(data.logs ?? []);
      requestAnimationFrame(scrollToBottom);
    } catch (error) {
      toast.notify(`读取日志失败：${(error as Error).message}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  const stopStream = useCallback(async () => {
    if (bridge && subIdRef.current) {
      try {
        await bridge.unsubscribeSSE(subIdRef.current);
      } catch {
        /* 断开失败不影响界面状态复位 */
      }
    }
    subIdRef.current = null;
    setStreaming(false);
  }, []);

  const toggleStream = async () => {
    if (!hasBridge()) return;
    if (streaming) {
      await stopStream();
      return;
    }
    try {
      const id = await bridge.subscribeSSE(
        'logs/stream',
        {
          onMessage(ev) {
            const entry = (ev && typeof ev.parsed === 'object' && ev.parsed
              ? ev.parsed
              : null) as LogEntry | null;
            if (!entry || !entry.time) return;
            setEntries((prev) => [...prev, entry]);
            requestAnimationFrame(scrollToBottom);
          },
          onError() {
            subIdRef.current = null;
            setStreaming(false);
          },
        },
        {},
      );
      subIdRef.current = id;
      setStreaming(true);
      toast.notify('实时日志流已开启');
    } catch (error) {
      toast.notify(`实时流开启失败：${(error as Error).message}`, 'error');
    }
  };

  const exportLogs = async () => {
    if (!hasBridge()) return;
    try {
      await bridge.download('logs/export', {}, 'multimodal_enhance_logs.txt');
      toast.notify('导出已开始下载');
    } catch (error) {
      toast.notify(`导出失败：${(error as Error).message}`, 'error');
    }
  };

  useEffect(() => {
    void load();
    // 页面卸载时必须退订，否则宿主侧会保留一个失效订阅
    return () => {
      if (subIdRef.current && bridge) {
        try {
          void bridge.unsubscribeSSE(subIdRef.current);
        } catch {
          /* ignore */
        }
      }
    };
  }, [load]);

  if (!hasBridge()) {
    return <EmptyState title="请从 AstrBot 插件页面打开" description="当前页面不在插件宿主环境内。" />;
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Button size="sm" variant="ghost" onClick={() => void load()} disabled={loading}>
            {loading ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
            刷新
          </Button>
          <Button
            size="sm"
            variant={streaming ? 'primary' : 'outline'}
            onClick={() => void toggleStream()}
          >
            <Radio className={cn('size-3.5', streaming && 'animate-pulse')} />
            {streaming ? '停止实时流' : '开启实时流'}
          </Button>
          <Button size="sm" variant="outline" onClick={() => void exportLogs()}>
            <Download className="size-3.5" />
            导出
          </Button>
        </div>
        <div className="text-[11.5px] text-muted-foreground">共 {entries.length} 条</div>
      </div>

      <pre
        ref={viewRef}
        className="max-h-[65vh] min-h-[320px] overflow-auto rounded-xl border border-border/60 bg-[var(--log-bg,theme(colors.zinc.950))] p-3 font-mono text-[11.5px] leading-relaxed text-zinc-200"
      >
        {entries.length ? (
          entries.map((entry, index) => (
            <div key={index} className="whitespace-pre-wrap break-all">
              <span className="text-zinc-500">[{entry.time}]</span>
              <span className={cn('ml-1', LEVEL_TONE[entry.level?.toUpperCase()] ?? 'text-zinc-400')}>
                [{entry.level}]
              </span>
              <span className="ml-1">{entry.msg}</span>
            </div>
          ))
        ) : (
          '（暂无日志）'
        )}
      </pre>
    </div>
  );
}
