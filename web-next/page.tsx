"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import de from "./i18n/de.json";
import en from "./i18n/en.json";
import pl from "./i18n/pl.json";

type Lang = "pl" | "en" | "de";
type ApiLang = "pl" | "en" | "other";
type Channel = "all" | "x" | "github" | "blog";
type Tab = "radar" | "config" | "integrations";
type DiscoveryMode = "stub" | "hybrid" | "live";
type IntegrationId = "github_publish" | "rss" | "hn" | "arxiv" | "x";

type Candidate = {
  id: string;
  source: string;
  url: string;
  topic: string;
  summary: string;
  language: ApiLang;
  score: number;
  age_minutes: number;
  reasons: string[];
};

type CandidatesResponse = {
  count: number;
  items: Candidate[];
};

type DraftVariant = {
  channel: "x" | "github" | "blog";
  language: "pl" | "en";
  content: string;
};

type DraftBundle = {
  draft_id: string;
  candidate_id: string;
  variants: DraftVariant[];
};

type QueueItem = {
  item_id: string;
  draft_id: string;
  target_channel: "x" | "github" | "blog";
  status: "draft" | "ready" | "queued" | "published" | "failed" | "cancelled";
  created_at: string;
  updated_at: string;
};

type QueueResponse = {
  count: number;
  items: QueueItem[];
};

type AuditItem = {
  id: string;
  actor: string;
  action: string;
  status: string;
  payload_hash: string;
  timestamp: string;
};

type AuditResponse = {
  count: number;
  items: AuditItem[];
};

type StrategyConfig = {
  id: string;
  name: string;
  discovery_mode: DiscoveryMode;
  rss_urls: string[];
  cache_ttl_seconds: number;
  min_score: number;
  limit: number;
  active_channels: Array<"x" | "github" | "blog">;
  draft_languages: Array<"pl" | "en">;
};

type ConfigResponse = {
  active_strategy_id: string;
  active_strategy: StrategyConfig;
};

type StrategiesResponse = {
  active_strategy_id: string;
  items: StrategyConfig[];
};

type IntegrationDescriptor = {
  id: IntegrationId;
  name: string;
  requires_key: boolean;
  status: "configured" | "missing" | "invalid";
  details: string;
  key_hint?: string | null;
  masked_secret?: string | null;
  configured_target?: string | null;
};

type IntegrationsResponse = {
  items: IntegrationDescriptor[];
};

type IntegrationTestResponse = {
  id: IntegrationId;
  success: boolean;
  status: "configured" | "missing" | "invalid";
  tested_at: string;
  message: string;
};

const dict: Record<Lang, Record<string, string>> = { pl, en, de };

const DEFAULT_FORM: StrategyConfig = {
  id: "",
  name: "",
  discovery_mode: "hybrid",
  rss_urls: [],
  cache_ttl_seconds: 1800,
  min_score: 0.3,
  limit: 30,
  active_channels: ["x", "github", "blog"],
  draft_languages: ["pl", "en"],
};

export default function BrandStudioPage() {
  const [tab, setTab] = useState<Tab>("radar");
  const [lang, setLang] = useState<Lang>("en");
  const [apiLang, setApiLang] = useState<"all" | ApiLang>("all");
  const [channel, setChannel] = useState<Channel>("all");
  const [minScore, setMinScore] = useState(0.3);
  const [items, setItems] = useState<Candidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string>("");
  const [draft, setDraft] = useState<DraftBundle | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [queueLoading, setQueueLoading] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [audit, setAudit] = useState<AuditItem[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);

  const [strategies, setStrategies] = useState<StrategyConfig[]>([]);
  const [activeStrategyId, setActiveStrategyId] = useState<string>("");
  const [configForm, setConfigForm] = useState<StrategyConfig>(DEFAULT_FORM);
  const [configLoading, setConfigLoading] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);
  const [strategyName, setStrategyName] = useState("");

  const [integrations, setIntegrations] = useState<IntegrationDescriptor[]>([]);
  const [integrationLoading, setIntegrationLoading] = useState(false);
  const [integrationError, setIntegrationError] = useState<string | null>(null);
  const [integrationTests, setIntegrationTests] = useState<Record<string, string>>({});

  useEffect(() => {
    const raw = (typeof navigator !== "undefined" ? navigator.language : "en").toLowerCase();
    if (raw.startsWith("pl")) {
      setLang("pl");
      return;
    }
    if (raw.startsWith("de")) {
      setLang("de");
      return;
    }
    setLang("en");
  }, []);

  const t = useCallback(
    (key: string): string => {
      return dict[lang][key] ?? dict.en[key] ?? key;
    },
    [lang]
  );

  const normalizeRssUrls = useCallback((raw: string): string[] => {
    return raw
      .split("\n")
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
  }, []);

  const loadCandidates = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      params.set("limit", "30");
      params.set("min_score", String(minScore));
      if (apiLang !== "all") {
        params.set("lang", apiLang);
      }
      if (channel !== "all") {
        params.set("channel", channel);
      }

      const response = await fetch(`/api/v1/brand-studio/sources/candidates?${params.toString()}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = (await response.json()) as CandidatesResponse;
      setItems(payload.items);
      if (payload.items.length && !selectedCandidateId) {
        setSelectedCandidateId(payload.items[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setLoading(false);
    }
  }, [apiLang, channel, minScore, selectedCandidateId]);

  const loadQueue = useCallback(async () => {
    setQueueLoading(true);
    setQueueError(null);
    try {
      const response = await fetch("/api/v1/brand-studio/queue", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = (await response.json()) as QueueResponse;
      setQueue(payload.items);
    } catch (err) {
      setQueueError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setQueueLoading(false);
    }
  }, []);

  const loadAudit = useCallback(async () => {
    setAuditLoading(true);
    setAuditError(null);
    try {
      const response = await fetch("/api/v1/brand-studio/audit", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = (await response.json()) as AuditResponse;
      setAudit(payload.items);
    } catch (err) {
      setAuditError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setAuditLoading(false);
    }
  }, []);

  const loadConfig = useCallback(async () => {
    setConfigLoading(true);
    setConfigError(null);
    try {
      const [configResp, strategiesResp] = await Promise.all([
        fetch("/api/v1/brand-studio/config", { cache: "no-store" }),
        fetch("/api/v1/brand-studio/strategies", { cache: "no-store" }),
      ]);
      if (!configResp.ok || !strategiesResp.ok) {
        throw new Error(`HTTP ${configResp.status}/${strategiesResp.status}`);
      }
      const configPayload = (await configResp.json()) as ConfigResponse;
      const strategiesPayload = (await strategiesResp.json()) as StrategiesResponse;
      setActiveStrategyId(configPayload.active_strategy_id);
      setConfigForm(configPayload.active_strategy);
      setStrategies(strategiesPayload.items);
      setStrategyName(configPayload.active_strategy.name);
      setMinScore(configPayload.active_strategy.min_score);
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setConfigLoading(false);
    }
  }, []);

  const loadIntegrations = useCallback(async () => {
    setIntegrationLoading(true);
    setIntegrationError(null);
    try {
      const response = await fetch("/api/v1/brand-studio/integrations", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = (await response.json()) as IntegrationsResponse;
      setIntegrations(payload.items);
    } catch (err) {
      setIntegrationError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setIntegrationLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCandidates();
  }, [loadCandidates]);

  useEffect(() => {
    void loadQueue();
    void loadAudit();
    void loadConfig();
    void loadIntegrations();
  }, [loadQueue, loadAudit, loadConfig, loadIntegrations]);

  const stats = useMemo(() => {
    if (!items.length) {
      return { count: 0, topScore: 0, freshest: "-" };
    }
    const topScore = Math.max(...items.map((item) => item.score));
    const freshestMinutes = Math.min(...items.map((item) => item.age_minutes));
    return { count: items.length, topScore, freshest: `${freshestMinutes}m` };
  }, [items]);

  const generateDraft = useCallback(async () => {
    if (!selectedCandidateId) {
      return;
    }
    setDraftLoading(true);
    setDraftError(null);
    try {
      const selectedChannels =
        channel === "all" ? (["x", "github", "blog"] as const) : ([channel] as const);
      const selectedLanguages =
        apiLang === "pl" || apiLang === "en" ? ([apiLang] as const) : (["pl", "en"] as const);
      const response = await fetch("/api/v1/brand-studio/drafts/generate", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Authenticated-User": "local-user",
        },
        body: JSON.stringify({
          candidate_id: selectedCandidateId,
          channels: selectedChannels,
          languages: selectedLanguages,
          tone: "expert",
        }),
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = (await response.json()) as DraftBundle;
      setDraft(payload);
      void loadAudit();
    } catch (err) {
      setDraftError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setDraftLoading(false);
    }
  }, [apiLang, channel, loadAudit, selectedCandidateId]);

  const queueVariant = useCallback(
    async (variant: DraftVariant) => {
      if (!draft) {
        return;
      }
      setQueueLoading(true);
      setQueueError(null);
      try {
        const response = await fetch(`/api/v1/brand-studio/drafts/${draft.draft_id}/queue`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Authenticated-User": "local-user",
          },
          body: JSON.stringify({
            target_channel: variant.channel,
            target_language: variant.language,
          }),
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        await loadQueue();
        await loadAudit();
      } catch (err) {
        setQueueError(err instanceof Error ? err.message : "unknown_error");
        setQueueLoading(false);
      }
    },
    [draft, loadAudit, loadQueue]
  );

  const publishNow = useCallback(
    async (itemId: string) => {
      setQueueLoading(true);
      setQueueError(null);
      try {
        const response = await fetch(`/api/v1/brand-studio/queue/${itemId}/publish`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Authenticated-User": "local-user",
          },
          body: JSON.stringify({ confirm_publish: true }),
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        await loadQueue();
        await loadAudit();
      } catch (err) {
        setQueueError(err instanceof Error ? err.message : "unknown_error");
        setQueueLoading(false);
      }
    },
    [loadAudit, loadQueue]
  );

  const saveConfig = useCallback(async () => {
    setConfigLoading(true);
    setConfigError(null);
    try {
      const payload = {
        discovery_mode: configForm.discovery_mode,
        rss_urls: configForm.rss_urls,
        cache_ttl_seconds: configForm.cache_ttl_seconds,
        min_score: configForm.min_score,
        limit: configForm.limit,
        active_channels: configForm.active_channels,
        draft_languages: configForm.draft_languages,
      };
      const response = await fetch("/api/v1/brand-studio/config", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-Authenticated-User": "local-user",
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      await loadConfig();
      await loadCandidates();
      await loadAudit();
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setConfigLoading(false);
    }
  }, [configForm, loadAudit, loadCandidates, loadConfig]);

  const saveStrategy = useCallback(async () => {
    if (!configForm.id) {
      return;
    }
    setConfigLoading(true);
    setConfigError(null);
    try {
      const response = await fetch(`/api/v1/brand-studio/strategies/${configForm.id}`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-Authenticated-User": "local-user",
        },
        body: JSON.stringify({
          name: strategyName,
          discovery_mode: configForm.discovery_mode,
          rss_urls: configForm.rss_urls,
          cache_ttl_seconds: configForm.cache_ttl_seconds,
          min_score: configForm.min_score,
          limit: configForm.limit,
          active_channels: configForm.active_channels,
          draft_languages: configForm.draft_languages,
        }),
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      await loadConfig();
      await loadAudit();
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setConfigLoading(false);
    }
  }, [configForm, loadAudit, loadConfig, strategyName]);

  const refreshCandidatesNow = useCallback(async () => {
    setConfigLoading(true);
    setConfigError(null);
    try {
      const response = await fetch("/api/v1/brand-studio/config/refresh", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Authenticated-User": "local-user",
        },
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      await loadCandidates();
      await loadAudit();
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setConfigLoading(false);
    }
  }, [loadAudit, loadCandidates]);

  const createStrategy = useCallback(async () => {
    const nextName = strategyName.trim();
    if (!nextName) {
      return;
    }
    setConfigLoading(true);
    setConfigError(null);
    try {
      const response = await fetch("/api/v1/brand-studio/strategies", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Authenticated-User": "local-user",
        },
        body: JSON.stringify({
          name: nextName,
          base_strategy_id: activeStrategyId,
        }),
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      await loadConfig();
      await loadAudit();
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setConfigLoading(false);
    }
  }, [activeStrategyId, loadAudit, loadConfig, strategyName]);

  const duplicateStrategy = useCallback(
    async (source: StrategyConfig) => {
      setConfigLoading(true);
      setConfigError(null);
      try {
        const response = await fetch("/api/v1/brand-studio/strategies", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Authenticated-User": "local-user",
          },
          body: JSON.stringify({
            name: `${source.name} copy`,
            base_strategy_id: source.id,
          }),
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        await loadConfig();
        await loadAudit();
      } catch (err) {
        setConfigError(err instanceof Error ? err.message : "unknown_error");
      } finally {
        setConfigLoading(false);
      }
    },
    [loadAudit, loadConfig]
  );

  const activateStrategy = useCallback(
    async (strategyId: string) => {
      setConfigLoading(true);
      setConfigError(null);
      try {
        const response = await fetch(`/api/v1/brand-studio/strategies/${strategyId}/activate`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Authenticated-User": "local-user",
          },
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        await loadConfig();
        await loadCandidates();
        await loadAudit();
      } catch (err) {
        setConfigError(err instanceof Error ? err.message : "unknown_error");
      } finally {
        setConfigLoading(false);
      }
    },
    [loadAudit, loadCandidates, loadConfig]
  );

  const deleteStrategy = useCallback(
    async (strategyId: string) => {
      setConfigLoading(true);
      setConfigError(null);
      try {
        const response = await fetch(`/api/v1/brand-studio/strategies/${strategyId}`, {
          method: "DELETE",
          headers: {
            "Content-Type": "application/json",
            "X-Authenticated-User": "local-user",
          },
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        await loadConfig();
        await loadCandidates();
        await loadAudit();
      } catch (err) {
        setConfigError(err instanceof Error ? err.message : "unknown_error");
      } finally {
        setConfigLoading(false);
      }
    },
    [loadAudit, loadCandidates, loadConfig]
  );

  const runIntegrationTest = useCallback(
    async (integrationId: IntegrationId) => {
      setIntegrationLoading(true);
      setIntegrationError(null);
      try {
        const response = await fetch(`/api/v1/brand-studio/integrations/${integrationId}/test`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Authenticated-User": "local-user",
          },
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const payload = (await response.json()) as IntegrationTestResponse;
        setIntegrationTests((previous) => ({
          ...previous,
          [integrationId]: `${payload.status}: ${payload.message}`,
        }));
        await loadIntegrations();
        await loadAudit();
      } catch (err) {
        setIntegrationError(err instanceof Error ? err.message : "unknown_error");
      } finally {
        setIntegrationLoading(false);
      }
    },
    [loadAudit, loadIntegrations]
  );

  const toggleChannel = (value: "x" | "github" | "blog") => {
    setConfigForm((previous) => {
      const exists = previous.active_channels.includes(value);
      const next = exists
        ? previous.active_channels.filter((item) => item !== value)
        : [...previous.active_channels, value];
      return { ...previous, active_channels: next };
    });
  };

  const toggleLanguage = (value: "pl" | "en") => {
    setConfigForm((previous) => {
      const exists = previous.draft_languages.includes(value);
      const next = exists
        ? previous.draft_languages.filter((item) => item !== value)
        : [...previous.draft_languages, value];
      return { ...previous, draft_languages: next };
    });
  };

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <p className="eyebrow">{t("eyebrow")}</p>
        <h1 className="text-3xl font-semibold text-white">{t("title")}</h1>
        <p className="text-zinc-400">{t("subtitle")}</p>
      </div>

      <section className="flex flex-wrap gap-2">
        {(["radar", "config", "integrations"] as const).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => setTab(value)}
            className={`rounded-xl border px-4 py-2 text-sm transition ${
              tab === value
                ? "border-cyan-400 bg-cyan-500/10 text-cyan-100"
                : "border-zinc-700 text-zinc-300 hover:border-zinc-500"
            }`}
          >
            {t(`tabs.${value}`)}
          </button>
        ))}
      </section>

      {tab === "radar" ? (
        <>
          <section className="grid gap-3 md:grid-cols-4">
            <div className="glass-panel rounded-2xl border border-cyan-500/20 p-4">
              <p className="text-xs uppercase text-zinc-400">{t("stats.count")}</p>
              <p className="text-2xl font-semibold text-white">{stats.count}</p>
            </div>
            <div className="glass-panel rounded-2xl border border-cyan-500/20 p-4">
              <p className="text-xs uppercase text-zinc-400">{t("stats.topScore")}</p>
              <p className="text-2xl font-semibold text-white">{stats.topScore.toFixed(2)}</p>
            </div>
            <div className="glass-panel rounded-2xl border border-cyan-500/20 p-4">
              <p className="text-xs uppercase text-zinc-400">{t("stats.freshest")}</p>
              <p className="text-2xl font-semibold text-white">{stats.freshest}</p>
            </div>
            <div className="flex items-end">
              <button
                type="button"
                onClick={() => void loadCandidates()}
                className="rounded-xl border border-cyan-500/30 px-4 py-2 text-sm text-cyan-100 transition hover:border-cyan-400"
              >
                {t("filters.refresh")}
              </button>
            </div>
          </section>

          <section className="glass-panel space-y-4 rounded-2xl border border-cyan-500/20 p-4">
            <div className="grid gap-3 md:grid-cols-3">
              <label className="space-y-1">
                <span className="text-xs uppercase text-zinc-400">{t("filters.language")}</span>
                <select
                  value={apiLang}
                  onChange={(event) => setApiLang(event.target.value as "all" | ApiLang)}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                >
                  <option value="all">{t("filters.allLanguages")}</option>
                  <option value="pl">PL</option>
                  <option value="en">EN</option>
                  <option value="other">OTHER</option>
                </select>
              </label>
              <label className="space-y-1">
                <span className="text-xs uppercase text-zinc-400">{t("filters.channel")}</span>
                <select
                  value={channel}
                  onChange={(event) => setChannel(event.target.value as Channel)}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                >
                  <option value="all">{t("filters.allChannels")}</option>
                  <option value="x">X</option>
                  <option value="github">GitHub</option>
                  <option value="blog">Blog</option>
                </select>
              </label>
              <label className="space-y-1">
                <span className="text-xs uppercase text-zinc-400">
                  {t("filters.minScore")}: {minScore.toFixed(2)}
                </span>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={minScore}
                  onChange={(event) => setMinScore(Number(event.target.value))}
                  className="w-full"
                />
              </label>
            </div>

            {loading ? <p className="text-zinc-400">{t("filters.loading")}</p> : null}
            {error ? <p className="text-rose-300">{t("filters.error")}</p> : null}
            {!loading && !error && !items.length ? <p className="text-zinc-400">{t("list.empty")}</p> : null}

            {!loading && !error && items.length ? (
              <div className="space-y-3">
                {items.map((item) => (
                  <article
                    key={item.id}
                    className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-4 text-sm"
                  >
                    <div className="mb-2 flex flex-wrap items-center gap-2 text-xs uppercase">
                      <span className="rounded bg-cyan-900/40 px-2 py-1 text-cyan-100">{item.source}</span>
                      <span className="rounded bg-zinc-800 px-2 py-1 text-zinc-200">{item.language}</span>
                      <span className="rounded bg-emerald-900/30 px-2 py-1 text-emerald-100">
                        score {item.score.toFixed(2)}
                      </span>
                      <span className="rounded bg-zinc-800 px-2 py-1 text-zinc-200">
                        {t("list.ageMinutes")}: {item.age_minutes}m
                      </span>
                    </div>
                    <h3 className="text-base font-medium text-zinc-100">{item.topic}</h3>
                    <p className="mt-1 text-zinc-300">{item.summary}</p>
                    <p className="mt-2 text-zinc-400">
                      {t("list.reasons")}: {item.reasons.join(", ")}
                    </p>
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-2 inline-flex text-cyan-300 hover:text-cyan-200"
                    >
                      {item.url}
                    </a>
                  </article>
                ))}
              </div>
            ) : null}
          </section>

          <section className="grid gap-4 lg:grid-cols-3">
            <div className="glass-panel space-y-3 rounded-2xl border border-emerald-500/20 p-4">
              <h2 className="text-lg font-medium text-emerald-100">{t("drafts.title")}</h2>
              <label className="space-y-1">
                <span className="text-xs uppercase text-zinc-400">{t("drafts.candidate")}</span>
                <select
                  value={selectedCandidateId}
                  onChange={(event) => setSelectedCandidateId(event.target.value)}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                >
                  {items.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.topic}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                onClick={() => void generateDraft()}
                disabled={draftLoading || !selectedCandidateId}
                className="rounded-xl border border-emerald-500/30 px-4 py-2 text-sm text-emerald-100 transition hover:border-emerald-400 disabled:opacity-50"
              >
                {draftLoading ? t("drafts.generating") : t("drafts.generate")}
              </button>
              {draftError ? <p className="text-rose-300">{draftError}</p> : null}
              {draft ? (
                <div className="space-y-2">
                  {draft.variants.map((variant, index) => (
                    <article key={`${variant.channel}-${variant.language}-${index}`} className="rounded-lg border border-zinc-800 p-3">
                      <p className="text-xs uppercase text-zinc-400">
                        {variant.channel} / {variant.language}
                      </p>
                      <p className="mt-1 text-sm text-zinc-200">{variant.content}</p>
                      <button
                        type="button"
                        onClick={() => void queueVariant(variant)}
                        className="mt-2 rounded-lg border border-zinc-700 px-3 py-1 text-xs text-zinc-200 hover:border-zinc-500"
                      >
                        {t("drafts.queue")}
                      </button>
                    </article>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="glass-panel space-y-3 rounded-2xl border border-violet-500/20 p-4">
              <h2 className="text-lg font-medium text-violet-100">{t("queue.title")}</h2>
              {queueLoading ? <p className="text-zinc-400">{t("queue.loading")}</p> : null}
              {queueError ? <p className="text-rose-300">{queueError}</p> : null}
              {!queueLoading && !queue.length ? <p className="text-zinc-400">{t("queue.empty")}</p> : null}
              <div className="space-y-2">
                {queue.map((item) => (
                  <article key={item.item_id} className="rounded-lg border border-zinc-800 p-3">
                    <p className="text-xs uppercase text-zinc-400">
                      {item.target_channel} / {item.status}
                    </p>
                    <p className="text-xs text-zinc-500">{item.item_id}</p>
                    <button
                      type="button"
                      onClick={() => void publishNow(item.item_id)}
                      disabled={item.status === "published" || queueLoading}
                      className="mt-2 rounded-lg border border-violet-500/30 px-3 py-1 text-xs text-violet-100 hover:border-violet-400 disabled:opacity-50"
                    >
                      {item.status === "published" ? t("queue.published") : t("queue.publishNow")}
                    </button>
                  </article>
                ))}
              </div>
            </div>

            <div className="glass-panel space-y-3 rounded-2xl border border-cyan-500/20 p-4">
              <h2 className="text-lg font-medium text-cyan-100">{t("audit.title")}</h2>
              {auditLoading ? <p className="text-zinc-400">{t("audit.loading")}</p> : null}
              {auditError ? <p className="text-rose-300">{auditError}</p> : null}
              {!auditLoading && !audit.length ? <p className="text-zinc-400">{t("audit.empty")}</p> : null}
              <div className="space-y-2">
                {audit.slice(0, 8).map((entry) => (
                  <article key={entry.id} className="rounded-lg border border-zinc-800 p-3">
                    <p className="text-xs uppercase text-zinc-400">
                      {entry.action} / {entry.status}
                    </p>
                    <p className="text-xs text-zinc-500">{entry.actor}</p>
                    <p className="text-xs text-zinc-500">{new Date(entry.timestamp).toLocaleString()}</p>
                  </article>
                ))}
              </div>
            </div>
          </section>
        </>
      ) : null}

      {tab === "config" ? (
        <section className="glass-panel space-y-4 rounded-2xl border border-emerald-500/20 p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1">
              <span className="text-xs uppercase text-zinc-400">{t("config.activeStrategy")}</span>
              <select
                value={activeStrategyId}
                onChange={(event) => {
                  const selectedId = event.target.value;
                  const found = strategies.find((item) => item.id === selectedId);
                  if (!found) {
                    return;
                  }
                  setActiveStrategyId(selectedId);
                  setConfigForm(found);
                  setStrategyName(found.name);
                }}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
              >
                {strategies.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-xs uppercase text-zinc-400">{t("config.strategyName")}</span>
              <input
                value={strategyName}
                onChange={(event) => setStrategyName(event.target.value)}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
              />
            </label>
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            <label className="space-y-1">
              <span className="text-xs uppercase text-zinc-400">{t("config.discoveryMode")}</span>
              <select
                value={configForm.discovery_mode}
                onChange={(event) =>
                  setConfigForm((previous) => ({
                    ...previous,
                    discovery_mode: event.target.value as DiscoveryMode,
                  }))
                }
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
              >
                <option value="stub">stub</option>
                <option value="hybrid">hybrid</option>
                <option value="live">live</option>
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-xs uppercase text-zinc-400">{t("config.cacheTtl")}</span>
              <input
                type="number"
                min={30}
                value={configForm.cache_ttl_seconds}
                onChange={(event) =>
                  setConfigForm((previous) => ({
                    ...previous,
                    cache_ttl_seconds: Number(event.target.value),
                  }))
                }
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs uppercase text-zinc-400">{t("config.limit")}</span>
              <input
                type="number"
                min={1}
                max={200}
                value={configForm.limit}
                onChange={(event) =>
                  setConfigForm((previous) => ({ ...previous, limit: Number(event.target.value) }))
                }
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
              />
            </label>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1">
              <span className="text-xs uppercase text-zinc-400">{t("config.minScore")}</span>
              <input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={configForm.min_score}
                onChange={(event) =>
                  setConfigForm((previous) => ({ ...previous, min_score: Number(event.target.value) }))
                }
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs uppercase text-zinc-400">{t("config.rss")}</span>
              <textarea
                value={configForm.rss_urls.join("\n")}
                onChange={(event) =>
                  setConfigForm((previous) => ({
                    ...previous,
                    rss_urls: normalizeRssUrls(event.target.value),
                  }))
                }
                rows={4}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
              />
            </label>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-2">
              <p className="text-xs uppercase text-zinc-400">{t("config.channels")}</p>
              <div className="flex flex-wrap gap-2">
                {(["x", "github", "blog"] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => toggleChannel(value)}
                    className={`rounded-lg border px-3 py-1 text-xs ${
                      configForm.active_channels.includes(value)
                        ? "border-emerald-400 text-emerald-200"
                        : "border-zinc-700 text-zinc-300"
                    }`}
                  >
                    {value}
                  </button>
                ))}
              </div>
            </div>
            <div className="space-y-2">
              <p className="text-xs uppercase text-zinc-400">{t("config.languages")}</p>
              <div className="flex flex-wrap gap-2">
                {(["pl", "en"] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => toggleLanguage(value)}
                    className={`rounded-lg border px-3 py-1 text-xs ${
                      configForm.draft_languages.includes(value)
                        ? "border-emerald-400 text-emerald-200"
                        : "border-zinc-700 text-zinc-300"
                    }`}
                  >
                    {value.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {configError ? <p className="text-rose-300">{configError}</p> : null}

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void saveConfig()}
              disabled={configLoading}
              className="rounded-xl border border-emerald-500/40 px-4 py-2 text-sm text-emerald-100 disabled:opacity-50"
            >
              {t("config.save")}
            </button>
            <button
              type="button"
              onClick={() => void saveStrategy()}
              disabled={configLoading || !configForm.id}
              className="rounded-xl border border-cyan-500/40 px-4 py-2 text-sm text-cyan-100 disabled:opacity-50"
            >
              {t("config.updateStrategy")}
            </button>
            <button
              type="button"
              onClick={() => void createStrategy()}
              disabled={configLoading || !strategyName.trim()}
              className="rounded-xl border border-violet-500/40 px-4 py-2 text-sm text-violet-100 disabled:opacity-50"
            >
              {t("config.newStrategy")}
            </button>
            <button
              type="button"
              onClick={() => {
                const source = strategies.find((item) => item.id === configForm.id);
                if (source) {
                  void duplicateStrategy(source);
                }
              }}
              disabled={configLoading || !configForm.id}
              className="rounded-xl border border-zinc-600 px-4 py-2 text-sm text-zinc-100 disabled:opacity-50"
            >
              {t("config.duplicate")}
            </button>
            <button
              type="button"
              onClick={() => void activateStrategy(configForm.id)}
              disabled={configLoading || !configForm.id}
              className="rounded-xl border border-amber-500/40 px-4 py-2 text-sm text-amber-100 disabled:opacity-50"
            >
              {t("config.activate")}
            </button>
            <button
              type="button"
              onClick={() => void deleteStrategy(configForm.id)}
              disabled={configLoading || !configForm.id}
              className="rounded-xl border border-rose-500/40 px-4 py-2 text-sm text-rose-100 disabled:opacity-50"
            >
              {t("config.delete")}
            </button>
            <button
              type="button"
              onClick={() => void refreshCandidatesNow()}
              disabled={configLoading}
              className="rounded-xl border border-cyan-500/30 px-4 py-2 text-sm text-cyan-100 disabled:opacity-50"
            >
              {t("config.refreshNow")}
            </button>
            <button
              type="button"
              onClick={() => void loadConfig()}
              disabled={configLoading}
              className="rounded-xl border border-zinc-700 px-4 py-2 text-sm text-zinc-100 disabled:opacity-50"
            >
              {t("config.restore")}
            </button>
          </div>
        </section>
      ) : null}

      {tab === "integrations" ? (
        <section className="glass-panel space-y-4 rounded-2xl border border-violet-500/20 p-4">
          {integrationLoading ? <p className="text-zinc-400">{t("integrations.loading")}</p> : null}
          {integrationError ? <p className="text-rose-300">{integrationError}</p> : null}
          {!integrationLoading && !integrations.length ? (
            <p className="text-zinc-400">{t("integrations.empty")}</p>
          ) : null}
          <div className="space-y-3">
            {integrations.map((item) => (
              <article key={item.id} className="rounded-xl border border-zinc-800 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-base font-medium text-zinc-100">{item.name}</h3>
                  <span className="rounded bg-zinc-800 px-2 py-1 text-xs uppercase text-zinc-200">
                    {item.status}
                  </span>
                  <span className="rounded bg-zinc-800 px-2 py-1 text-xs uppercase text-zinc-400">
                    {item.requires_key ? t("integrations.keyRequired") : t("integrations.public")}
                  </span>
                </div>
                <p className="mt-2 text-sm text-zinc-300">{item.details}</p>
                {item.key_hint ? (
                  <p className="mt-1 text-xs text-zinc-500">
                    {t("integrations.key")}: {item.key_hint}
                  </p>
                ) : null}
                {item.masked_secret ? (
                  <p className="mt-1 text-xs text-zinc-500">
                    {t("integrations.masked")} {item.masked_secret}
                  </p>
                ) : null}
                {item.configured_target ? (
                  <p className="mt-1 text-xs text-zinc-500">target: {item.configured_target}</p>
                ) : null}
                <button
                  type="button"
                  onClick={() => void runIntegrationTest(item.id)}
                  disabled={integrationLoading}
                  className="mt-3 rounded-lg border border-violet-500/40 px-3 py-1 text-xs text-violet-100 disabled:opacity-50"
                >
                  {t("integrations.test")}
                </button>
                {integrationTests[item.id] ? (
                  <p className="mt-2 text-xs text-cyan-200">{integrationTests[item.id]}</p>
                ) : null}
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
