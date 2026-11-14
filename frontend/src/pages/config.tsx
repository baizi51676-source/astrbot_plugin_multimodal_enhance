// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, Plus, RotateCcw, Save, Trash2, Undo2 } from 'lucide-react';
import {
  bridge,
  hasBridge,
  type BotRule,
  type ConfigMetaItem,
  type ConfigPayload,
  type ProviderOption,
} from '@/lib/bridge';
import { Badge, Button } from '@/components/ui/button';
import { Field, Input, Textarea } from '@/components/ui/field';
import { Select } from '@/components/ui/select';
import { ToggleSwitch } from '@/components/ui/toggle-switch';
import { EmptyState } from '@/components/ui/empty-state';
import { Dialog } from '@/components/ui/dialog';
import { useToast } from '@/components/ui/toast';

type Draft = {
  index: number;
  name: string;
  targets: string;
  overrides: Record<string, unknown>;
};

function metaGroups(meta: ConfigMetaItem[]) {
  const map = new Map<string, ConfigMetaItem[]>();
  for (const item of meta) {
    if (!map.has(item.group)) map.set(item.group, []);
    map.get(item.group)!.push(item);
  }
  return [...map.entries()];
}

function asNumber(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

/** 单个配置项的控件；所有控件都是受控组件，值由父级状态驱动。 */
function Control({
  item,
  value,
  disabled,
  secretSet,
  providers,
  onChange,
}: {
  item: ConfigMetaItem;
  value: unknown;
  disabled: boolean;
  secretSet: boolean;
  providers: ProviderOption[];
  onChange: (next: unknown) => void;
}) {
  if (item.type === 'bool') {
    return (
      <ToggleSwitch
        value={!!value}
        disabled={disabled}
        ariaLabel={item.label}
        onChange={(next) => onChange(next)}
      />
    );
  }
  if (item.type === 'int') {
    return (
      <Input
        type="number"
        value={String(asNumber(value))}
        disabled={disabled}
        onChange={(e) => onChange(parseInt(e.target.value || '0', 10) || 0)}
      />
    );
  }
  if (item.type === 'secret') {
    return (
      <Input
        type="password"
        value=""
        disabled={disabled}
        autoComplete="new-password"
        placeholder={secretSet ? '已设置，留空保持不变' : '未设置'}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  if (item.type === 'select' || item.type === 'provider') {
    const options =
      item.type === 'select'
        ? (item.options ?? [])
        : [{ value: '', label: '（跟随 AstrBot 设置）' }, ...providers.map((p) => ({ value: p.id, label: p.label }))];
    return (
      <Select
        value={String(value ?? '')}
        disabled={disabled}
        options={options}
        ariaLabel={item.label}
        onChange={(next) => onChange(next)}
      />
    );
  }
  if (item.type === 'textarea' || item.type === 'json') {
    const text = item.type === 'json' ? JSON.stringify(value ?? [], null, 2) : String(value ?? '');
    return (
      <Textarea
        value={text}
        disabled={disabled}
        rows={4}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  return (
    <Input
      value={String(value ?? '')}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export default function ConfigPage() {
  const toast = useToast();
  const [payload, setPayload] = useState<ConfigPayload | null>(null);
  const [providers, setProviders] = useState<ProviderOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState<string>('default');
  const [globalDraft, setGlobalDraft] = useState<Record<string, unknown>>({});
  const [botDraft, setBotDraft] = useState<Draft | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const load = useCallback(async () => {
    if (!hasBridge()) return;
    setLoading(true);
    try {
      const [cfg, prov] = await Promise.all([
        bridge.apiGet('config') as Promise<ConfigPayload>,
        bridge.apiGet('providers') as Promise<{ providers: ProviderOption[] }>,
      ]);
      setPayload(cfg);
      setProviders(prov.providers ?? []);
      setGlobalDraft({ ...(cfg.config ?? {}) });
      setBotDraft(null);
    } catch (error) {
      toast.notify(`读取配置失败：${(error as Error).message}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const meta = payload?.meta ?? [];
  const bots = payload?.bots ?? [];
  const secretsSet = payload?.secrets_set ?? {};
  const groups = useMemo(() => metaGroups(meta), [meta]);

  // 切换 Bot 时重建草稿
  useEffect(() => {
    if (tab === 'default') {
      setBotDraft(null);
      return;
    }
    const bot: BotRule | undefined = bots[Number(tab)];
    if (!bot) {
      setTab('default');
      return;
    }
    setBotDraft({
      index: Number(tab),
      name: bot.name ?? '',
      targets: (bot.targets ?? []).join(', '),
      overrides: JSON.parse(JSON.stringify(bot.overrides ?? {})) as Record<string, unknown>,
    });
  }, [tab, bots]);

  const saveGlobal = async () => {
    if (!hasBridge()) return;
    setSaving(true);
    try {
      const res = (await bridge.apiPost('config', { patch: globalDraft })) as { saved?: boolean };
      toast.notify(
        res.saved ? '默认配置已保存并同步' : '配置已更新（落盘失败，仅本次生效）',
        res.saved ? 'success' : 'error',
      );
      await load();
    } catch (error) {
      toast.notify(`保存失败：${(error as Error).message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const saveBot = async () => {
    if (!hasBridge() || !botDraft) return;
    setSaving(true);
    const targets = botDraft.targets
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      const res = (await bridge.apiPost('bots/save', {
        index: botDraft.index,
        name: botDraft.name,
        bots: targets,
        overrides: botDraft.overrides,
      })) as { saved?: boolean };
      toast.notify(
        res.saved ? 'Bot 配置已保存并同步' : '已更新（落盘失败，仅本次生效）',
        res.saved ? 'success' : 'error',
      );
      await load();
    } catch (error) {
      toast.notify(`保存失败：${(error as Error).message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const addBot = async () => {
    if (!hasBridge()) return;
    try {
      const res = (await bridge.apiPost('bots/add', { name: '新Bot', bots: [] })) as { index?: number };
      await load();
      setTab(String(res.index ?? Math.max(0, bots.length)));
      toast.notify('已新增 Bot 配置，请填写备注名与匹配目标');
    } catch (error) {
      toast.notify(`新增失败：${(error as Error).message}`, 'error');
    }
  };

  const deleteBot = async () => {
    if (!hasBridge() || !botDraft) return;
    setDeleteOpen(false);
    try {
      await bridge.apiPost('bots/delete', { index: botDraft.index });
      setTab('default');
      await load();
      toast.notify('已删除');
    } catch (error) {
      toast.notify(`删除失败：${(error as Error).message}`, 'error');
    }
  };

  if (!hasBridge()) {
    return <EmptyState title="请从 AstrBot 插件页面打开" description="当前页面不在插件宿主环境内。" />;
  }

  const isDefault = tab === 'default';

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <Button size="sm" variant={isDefault ? 'primary' : 'ghost'} onClick={() => setTab('default')}>
            默认配置
          </Button>
          {bots.map((bot, index) => (
            <Button
              key={index}
              size="sm"
              variant={tab === String(index) ? 'primary' : 'ghost'}
              onClick={() => setTab(String(index))}
            >
              {bot.name || `Bot ${index + 1}`}
            </Button>
          ))}
          <Button size="sm" variant="outline" onClick={() => void addBot()}>
            <Plus className="size-3.5" />
            新增
          </Button>
        </div>
        <div className="flex items-center gap-1.5">
          <Button size="sm" variant="ghost" onClick={() => void load()} disabled={loading}>
            <RotateCcw className="size-3.5" />
            重新读取
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={saving}
            onClick={() => void (isDefault ? saveGlobal() : saveBot())}
          >
            {saving ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
            保存
          </Button>
        </div>
      </div>

      <p className="text-[12px] leading-relaxed text-muted-foreground">
        默认配置 + 每个 Bot 分页覆写（未覆写的项自动跟随默认）；与 AstrBot 插件配置为同一份文件，双向同步。
      </p>

      {loading && !payload ? (
        <div className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />
          加载中…
        </div>
      ) : isDefault ? (
        <div className="flex flex-col gap-4">
          {groups.map(([group, items]) => (
            <div key={group} className="rounded-xl border border-border/60 bg-card p-4">
              <div className="mb-3 text-sm font-semibold text-foreground">{group}</div>
              <div className="grid gap-3 md:grid-cols-2">
                {items.map((item) => (
                  <Field key={item.key} label={item.label} hint={item.hint}>
                    <Control
                      item={item}
                      value={globalDraft[item.key]}
                      disabled={false}
                      secretSet={!!secretsSet[item.key]}
                      providers={providers}
                      onChange={(next) => setGlobalDraft((prev) => ({ ...prev, [item.key]: next }))}
                    />
                  </Field>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : botDraft ? (
        <div className="flex flex-col gap-4">
          <div className="rounded-xl border border-border/60 bg-card p-4">
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="备注名" hint="仅用于本页展示，方便区分不同 Bot。">
                <Input
                  value={botDraft.name}
                  placeholder="例如：主账号"
                  onChange={(e) => setBotDraft({ ...botDraft, name: e.target.value })}
                />
              </Field>
              <Field label="匹配目标" hint="平台实例 ID 或 QQ 号，逗号分隔。">
                <Input
                  value={botDraft.targets}
                  placeholder="例如：snowluma, 1234567890"
                  onChange={(e) => setBotDraft({ ...botDraft, targets: e.target.value })}
                />
              </Field>
            </div>
            <div className="mt-3">
              <Button size="sm" variant="destructive" onClick={() => setDeleteOpen(true)}>
                <Trash2 className="size-3.5" />
                删除此 Bot 配置
              </Button>
            </div>
          </div>

          <div className="flex flex-col gap-4">
            {groups.map(([group, items]) => {
              const visible = items.filter((i) => i.scope !== 'system');
              if (!visible.length) return null;
              return (
                <div key={group} className="rounded-xl border border-border/60 bg-card p-4">
                  <div className="mb-3 text-sm font-semibold text-foreground">{group}</div>
                  <div className="grid gap-3 md:grid-cols-2">
                    {visible.map((item) => {
                      const overridden = Object.prototype.hasOwnProperty.call(
                        botDraft.overrides,
                        item.key,
                      );
                      return (
                        <Field key={item.key} label={item.label} hint={item.hint}>
                          <div className="flex items-center gap-1.5">
                            <Badge tone={overridden ? 'accent' : 'muted'}>
                              {overridden ? '已覆写' : '跟随默认'}
                            </Badge>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => {
                                const next = { ...botDraft.overrides };
                                if (overridden) delete next[item.key];
                                else next[item.key] = globalDraft[item.key];
                                setBotDraft({ ...botDraft, overrides: next });
                              }}
                            >
                              {overridden ? (
                                <>
                                  <Undo2 className="size-3" />
                                  恢复默认
                                </>
                              ) : (
                                '覆写此值'
                              )}
                            </Button>
                          </div>
                          <Control
                            item={item}
                            value={overridden ? botDraft.overrides[item.key] : globalDraft[item.key]}
                            disabled={!overridden}
                            secretSet={!!secretsSet[item.key]}
                            providers={providers}
                            onChange={(next) =>
                              setBotDraft({
                                ...botDraft,
                                overrides: { ...botDraft.overrides, [item.key]: next },
                              })
                            }
                          />
                        </Field>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      <Dialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title="删除 Bot 配置"
        description={`确认删除「${botDraft?.name || '该 Bot'}」的覆写配置？该操作不可撤销，删除后该 Bot 将全部跟随默认配置。`}
        footer={
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setDeleteOpen(false)}>
              取消
            </Button>
            <Button size="sm" variant="destructive" onClick={() => void deleteBot()}>
              确认删除
            </Button>
          </div>
        }
      />
    </div>
  );
}
