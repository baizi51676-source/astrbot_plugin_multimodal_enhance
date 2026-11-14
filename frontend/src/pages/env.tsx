// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import { useCallback, useEffect, useRef, useState } from 'react';
import { Ban, Loader2, RefreshCw, Trash2, Wrench } from 'lucide-react';
import { bridge, hasBridge, type EnvState, type InstallState } from '@/lib/bridge';
import { Badge, Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { useToast } from '@/components/ui/toast';
import { cn } from '@/lib/utils';

function EnvRow({ name, ok, detail }: { name: string; ok: boolean; detail?: string }) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-border/60 bg-card px-3 py-2.5">
      <div className="min-w-0">
        <div className="text-[13px] font-medium text-foreground">{name}</div>
        <div className="mt-0.5 truncate text-[11.5px] text-muted-foreground">
          {detail || (ok ? '已安装' : '未找到')}
        </div>
      </div>
      <Badge tone={ok ? 'success' : 'warning'}>{ok ? '可用' : '缺失'}</Badge>
    </div>
  );
}

export default function EnvPage() {
  const toast = useToast();
  const [env, setEnv] = useState<EnvState | null>(null);
  const [install, setInstall] = useState<InstallState | null>(null);
  const [loading, setLoading] = useState(true);
  const [cancelling, setCancelling] = useState(false);
  const timerRef = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const load = useCallback(async () => {
    if (!hasBridge()) return;
    setLoading(true);
    try {
      const data = (await bridge.apiGet('env/check')) as EnvState;
      setEnv(data);
      setInstall(data.install ?? null);
      if (data.install?.running) startPolling();
    } catch (error) {
      toast.notify(`环境检测失败：${(error as Error).message}`, 'error');
    } finally {
      setLoading(false);
    }
    // startPolling 定义在下方，这里通过 ref 间接引用避免循环依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toast]);

  const startPolling = useCallback(() => {
    const api = bridge;
    if (!api || timerRef.current !== null) return;
    timerRef.current = window.setInterval(async () => {
      try {
        const state = (await api.apiGet('env/install/status')) as InstallState;
        setInstall(state);
        if (!state.running) {
          stopPolling();
          toast.notify(
            state.ok ? '依赖安装完成' : '依赖安装失败，详见输出',
            state.ok ? 'success' : 'error',
          );
          void load();
        }
      } catch {
        stopPolling();
      }
    }, 2500);
  }, [toast, stopPolling, load]);

  useEffect(() => {
    void load();
    return () => stopPolling();
  }, [load, stopPolling]);

  const oneClick = async () => {
    if (!hasBridge()) return;
    try {
      const res = (await bridge.apiPost('env/install', { mode: 'one_click' })) as {
        message?: string;
      };
      toast.notify(res.message || '一键配置已启动');
      startPolling();
    } catch (error) {
      toast.notify(`启动失败：${(error as Error).message}`, 'error');
    }
  };

  const cancelInstall = async () => {
    if (!hasBridge()) return;
    setCancelling(true);
    try {
      const res = (await bridge.apiPost('env/install/cancel', {})) as {
        cancelled?: boolean;
        message?: string;
      };
      toast.notify(
        res.message || (res.cancelled ? '已取消安装' : '当前没有进行中的安装任务'),
        res.cancelled ? 'success' : 'info',
      );
      void load();
    } catch (error) {
      toast.notify(`取消失败：${(error as Error).message}`, 'error');
    } finally {
      setCancelling(false);
    }
  };

  const cleanTemp = async () => {
    if (!hasBridge()) return;
    try {
      const res = (await bridge.apiPost('env/clean', {})) as {
        removed_files?: number;
        removed_human?: string;
      };
      toast.notify(`已清理 ${res.removed_files || 0} 个文件（${res.removed_human || '0B'}）`);
      void load();
    } catch (error) {
      toast.notify(`清理失败：${(error as Error).message}`, 'error');
    }
  };

  if (!hasBridge()) {
    return <EmptyState title="请从 AstrBot 插件页面打开" description="当前页面不在插件宿主环境内。" />;
  }

  const modules = env?.modules ?? {};
  const disk = env?.disk ?? {};

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-semibold text-foreground">环境检测</div>
        <div className="flex items-center gap-1.5">
          <Button size="sm" variant="ghost" onClick={() => void load()} disabled={loading}>
            {loading ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
            重新检测
          </Button>
          <Button size="sm" variant="outline" onClick={() => void cleanTemp()}>
            <Trash2 className="size-3.5" />
            清理临时文件
          </Button>
        </div>
      </div>

      <div className="grid gap-2 md:grid-cols-2">
        <EnvRow
          name="ffmpeg"
          ok={!!env?.ffmpeg?.ok}
          detail={[env?.ffmpeg?.path, env?.ffmpeg?.version].filter(Boolean).join(' · ')}
        />
        <EnvRow name="ffprobe" ok={!!env?.ffprobe?.ok} detail={env?.ffprobe?.path} />
        <EnvRow name="yt-dlp（B站解析）" ok={!!env?.ytdlp?.ok} detail={env?.ytdlp?.version} />
        <EnvRow name="Python: numpy（频谱分析必需）" ok={!!modules.numpy} />
        <EnvRow name="Python: librosa（音频深度分析）" ok={!!modules.librosa} />
        <EnvRow name="Python: scipy" ok={!!modules.scipy} />
        <EnvRow name="Python: soundfile" ok={!!modules.soundfile} />
        <EnvRow
          name="磁盘剩余 / 工作目录"
          ok
          detail={`${disk.free_human || '?'} / ${disk.total_human || '?'} · ${env?.workdir || ''}`}
        />
      </div>

      <div className="rounded-xl border border-border/60 bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Wrench className="size-4 text-muted-foreground" />
            一键配置所有功能依赖
          </div>
          <div className="flex items-center gap-1.5">
            <Button size="sm" variant="primary" onClick={() => void oneClick()}>
              一键配置
            </Button>
            {install?.running ? (
              <Button
                size="sm"
                variant="outline"
                disabled={cancelling}
                onClick={() => void cancelInstall()}
              >
                <Ban className="size-3.5" />
                取消安装
              </Button>
            ) : null}
          </div>
        </div>
        <p className="mt-2 text-[11.5px] leading-relaxed text-muted-foreground">
          统一配置：系统工具 ffmpeg / ffprobe（apt）＋ Python 依赖 numpy / librosa / scipy /
          soundfile（pip）＋ yt-dlp（pip）；不检测缺失情况，全部安装/升级。安装期间请勿关闭页面。
        </p>

        <pre
          className={cn(
            'mt-3 max-h-[40vh] min-h-[120px] overflow-auto rounded-lg border border-border/60 bg-[var(--log-bg,theme(colors.zinc.950))] p-3 font-mono text-[11.5px] leading-relaxed text-zinc-200',
          )}
        >
          {!install || (!install.running && !install.started_at)
            ? '（暂无安装任务）'
            : `${[
                `开始时间：${install.started_at || '-'} · 状态：${
                  install.running ? '安装中…' : install.ok ? '安装成功' : '安装失败'
                }`,
                `包：${(install.packages || []).join(', ')}`,
                '',
              ].join('\n')}${install.output || ''}`}
        </pre>
      </div>
    </div>
  );
}
