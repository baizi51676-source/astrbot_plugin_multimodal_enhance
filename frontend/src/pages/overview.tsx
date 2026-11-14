// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react';
import { bridge, hasBridge, type AppState, type AppStateBot } from '@/lib/bridge';
import { Badge, Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { useToast } from '@/components/ui/toast';
import { cn } from '@/lib/utils';

function StatCard({
  label,
  value,
  tone = 'default',
}: {
  label: string;
  value: React.ReactNode;
  tone?: 'default' | 'ok' | 'warn' | 'muted';
}) {
  const toneClass =
    tone === 'ok'
      ? 'text-emerald-600 dark:text-emerald-400'
      : tone === 'warn'
        ? 'text-amber-600 dark:text-amber-400'
        : tone === 'muted'
          ? 'text-muted-foreground'
          : 'text-foreground';
  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-xs">
      <div className="text-[11.5px] font-medium tracking-wide text-muted-foreground">{label}</div>
      <div className={cn('mt-2 text-[15px] font-semibold leading-snug', toneClass)}>{value}</div>
    </div>
  );
}

function FeatureBadge({ on }: { on: boolean }) {
  return on ? (
    <Badge tone="success">已启用</Badge>
  ) : (
    <Badge tone="muted">未启用</Badge>
  );
}

export default function OverviewPage() {
  const toast = useToast();
  const [state, setState] = useState<AppState | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<string>('default');

  const load = useCallback(async () => {
    if (!hasBridge()) return;
    setLoading(true);
    try {
      const data = (await bridge.apiGet('state')) as AppState;
      setState(data);
    } catch (error) {
      toast.notify(`读取总览失败：${(error as Error).message}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!hasBridge()) {
    return <EmptyState title="请从 AstrBot 插件页面打开" description="当前页面不在插件宿主环境内。" />;
  }

  const bots = state?.bots ?? [];
  const isDefault = tab === 'default';
  const bot: AppStateBot | null = isDefault ? null : (bots[Number(tab)] ?? null);
  const features = isDefault ? state?.features : bot?.features;
  const errors = state?.recent_errors ?? [];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant={isDefault ? 'primary' : 'ghost'}
            onClick={() => setTab('default')}
          >
            默认
          </Button>
          {bots.map((item, index) => (
            <Button
              key={index}
              size="sm"
              variant={tab === String(index) ? 'primary' : 'ghost'}
              onClick={() => setTab(String(index))}
            >
              {item.name || `Bot ${index + 1}`}
            </Button>
          ))}
        </div>
        <Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}>
          {loading ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
          刷新
        </Button>
      </div>

      {loading && !state ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="rounded-xl border border-border/60 bg-card p-4">
              <div className="sk-shimmer h-3 w-16 rounded" />
              <div className="sk-shimmer mt-3 h-5 w-24 rounded" />
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
            <StatCard
              label="当前视图"
              value={isDefault ? '默认配置（全局）' : bot?.name || '—'}
            />
            <StatCard
              label="总开关"
              value={state?.enabled ? '已启用' : '已停用'}
              tone={state?.enabled ? 'ok' : 'muted'}
            />
            <StatCard label="图片增强" value={<FeatureBadge on={!!features?.image} />} />
            <StatCard label="音频理解" value={<FeatureBadge on={!!features?.audio} />} />
            <StatCard label="视频理解" value={<FeatureBadge on={!!features?.video} />} />
            <StatCard label="分析中提示" value={<FeatureBadge on={!!features?.notice} />} />
            <StatCard
              label="图片转述注入"
              value={state?.caption_patch_installed ? '已安装' : '未安装'}
              tone={state?.caption_patch_installed ? 'ok' : 'warn'}
            />
            <StatCard
              label="图转文模型"
              value={state?.caption_provider || '（未配置）'}
              tone={state?.caption_provider ? 'default' : 'warn'}
            />
            <StatCard
              label="语音转文本"
              value={state?.stt_configured ? '已配置' : '（未配置）'}
              tone={state?.stt_configured ? 'default' : 'warn'}
            />
            <StatCard
              label="音频深度分析"
              value={state?.librosa ? 'librosa 可用' : '轻量模式'}
              tone={state?.librosa ? 'ok' : 'muted'}
            />
            <StatCard
              label="进行中的解析"
              value={String(state?.pipeline?.active_tasks ?? 0)}
              tone="muted"
            />
            <StatCard label="版本" value={state?.version || '—'} tone="muted" />
          </div>

          {!isDefault && bot ? (
            <div className="rounded-xl border border-border/60 bg-card p-4">
              <div className="text-sm font-semibold text-foreground">覆写项</div>
              <div className="mt-2 text-[12.5px] text-muted-foreground">
                匹配目标：{bot.targets.length ? bot.targets.join('、') : '（未设置）'}
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {Object.keys(bot.overrides).length ? (
                  Object.entries(bot.overrides).map(([key, value]) => (
                    <span
                      key={key}
                      className="rounded-md border border-border/70 bg-surface px-2 py-1 font-mono text-[11.5px] text-foreground"
                    >
                      {key} = {JSON.stringify(value)}
                    </span>
                  ))
                ) : (
                  <span className="text-[12.5px] text-muted-foreground">
                    未覆写任何项（全部跟随默认配置）
                  </span>
                )}
              </div>
            </div>
          ) : null}

          <div className="rounded-xl border border-border/60 bg-card p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <AlertTriangle className="size-4 text-muted-foreground" />
              最近错误日志
            </div>
            {errors.length ? (
              <ul className="mt-3 flex flex-col gap-1.5">
                {errors.map((item, index) => (
                  <li key={index} className="font-mono text-[11.5px] text-muted-foreground">
                    [{item.time}] {item.msg}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="mt-2 text-[12.5px] text-muted-foreground">暂无</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
