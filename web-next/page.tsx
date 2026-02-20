"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import de from "./i18n/de.json";
import en from "./i18n/en.json";
import pl from "./i18n/pl.json";

type Lang = "pl" | "en" | "de";
type ApiLang = "pl" | "en" | "other";
type PublishChannel =
  | "x"
  | "github"
  | "blog"
  | "linkedin"
  | "medium"
  | "hf_blog"
  | "hf_spaces"
  | "reddit"
  | "devto"
  | "hashnode";
type Channel = "all" | PublishChannel;
type Tab = "radar" | "monitoring" | "sources" | "keywords" | "campaigns" | "config" | "integrations";
type DiscoveryMode = "stub" | "hybrid" | "live";
type IntegrationId =
  | "github_publish"
  | "rss"
  | "hn"
  | "arxiv"
  | "x"
  | "devto_publish"
  | "reddit_publish"
  | "hashnode_publish"
  | "linkedin_publish"
  | "medium_publish"
  | "hf_blog_publish"
  | "hf_spaces_publish";

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
  channel: PublishChannel;
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
  target_channel: PublishChannel;
  account_id?: string | null;
  account_display_name?: string | null;
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
type LogOutcome = "success" | "warning" | "error";

type StrategyConfig = {
  id: string;
  name: string;
  discovery_mode: DiscoveryMode;
  rss_urls: string[];
  topic_keywords: string[];
  cache_ttl_seconds: number;
  min_score: number;
  limit: number;
  active_channels: Array<PublishChannel>;
  draft_languages: Array<"pl" | "en">;
  default_accounts: Partial<Record<PublishChannel, string>>;
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

type ChannelDescriptor = {
  id: PublishChannel;
  accounts_count: number;
  default_account_id?: string | null;
};

type ChannelsResponse = {
  items: ChannelDescriptor[];
};

type ChannelAccount = {
  account_id: string;
  channel: PublishChannel;
  display_name: string;
  target?: string | null;
  enabled: boolean;
  is_default: boolean;
  secret_status: "configured" | "missing" | "invalid";
  capabilities: string[];
  last_tested_at?: string | null;
  last_test_status?: "configured" | "missing" | "invalid" | null;
  last_test_message?: string | null;
  successful_publishes: number;
  failed_publishes: number;
  last_published_at?: string | null;
  last_publish_status?: "published" | "failed" | null;
  last_publish_message?: string | null;
};

type ChannelAccountsResponse = {
  channel: PublishChannel;
  items: ChannelAccount[];
};

type KeywordType = "brand_core" | "brand_product" | "brand_person" | "risk_term" | "competitor_context";
type SearchResultClass = "owned_source" | "brand_mention_positive" | "brand_mention_neutral" | "brand_mention_risk" | "unrelated";
type CampaignStatus = "draft" | "ready" | "running" | "completed" | "failed" | "cancelled";

type BrandKeyword = {
  keyword_id: string;
  phrase: string;
  keyword_type: KeywordType;
  priority: number;
  active: boolean;
  created_at: string;
};

type BrandBaseSource = {
  source_id: string;
  name: string;
  base_url: string;
  channel: PublishChannel;
  priority: number;
  enabled: boolean;
  owner_tag?: string | null;
  created_at: string;
};

type BrandSearchResult = {
  result_id: string;
  scan_id: string;
  keyword_id: string;
  url: string;
  title: string;
  snippet: string;
  position: number;
  scanned_at: string;
  classification: SearchResultClass;
  maps_to_base_source: boolean;
  base_source_id?: string | null;
};

type BrandMonitoringSummary = {
  total_keywords: number;
  active_keywords: number;
  total_base_sources: number;
  total_results: number;
  owned_source_coverage: number;
  risk_count: number;
  last_scan_at?: string | null;
};

type BrandCampaign = {
  campaign_id: string;
  name: string;
  strategy_id: string;
  source_scan_id?: string | null;
  linked_keyword_ids: string[];
  linked_result_ids: string[];
  channels: PublishChannel[];
  status: CampaignStatus;
  created_at: string;
  updated_at: string;
  draft_ids: string[];
  queue_ids: string[];
};

const CHANNELS: PublishChannel[] = [
  "x",
  "github",
  "blog",
  "linkedin",
  "medium",
  "hf_blog",
  "hf_spaces",
  "reddit",
  "devto",
  "hashnode",
];

const dict: Record<Lang, Record<string, string>> = { pl, en, de };

const MAX_CAMPAIGN_NAME_LENGTH = 60;

/** Extract a human-readable error message from a non-ok fetch Response. */
async function extractErrorMessage(resp: Response): Promise<string> {
  try {
    const body = (await resp.json()) as { detail?: string };
    return body.detail ?? `HTTP ${resp.status}`;
  } catch {
    return `HTTP ${resp.status}`;
  }
}

const DEFAULT_FORM: StrategyConfig = {
  id: "",
  name: "",
  discovery_mode: "hybrid",
  rss_urls: [],
  topic_keywords: [],
  cache_ttl_seconds: 1800,
  min_score: 0.3,
  limit: 30,
  active_channels: ["x", "github", "blog"],
  draft_languages: ["pl", "en"],
  default_accounts: {},
};

const PL_HELP: Record<string, string> = {
  "tabs.radar":
    "Radar to lista kandydatów tematów. Tu filtrujesz źródła i jakość okazji do publikacji.",
  "tabs.config":
    "Konfiguracja steruje strategią discovery i publikacji. Zmiany wpływają na kolejne odświeżenia i drafty.",
  "tabs.integrations":
    "API i klucze pokazuje stan integracji oraz kont kanałowych używanych do publikacji.",
  "stats.count": "Liczba kandydatów, które przeszły aktualne filtry.",
  "stats.topScore": "Najwyższy wynik jakości/relewancji w aktualnej liście kandydatów.",
  "stats.freshest": "Wiek czasowy najświeższego kandydata po filtrach.",
  "filters.language": "Filtr języka kandydata (PL/EN/other).",
  "filters.channel": "Filtr źródła/kanału, z którego pochodzi kandydat.",
  "filters.minScore": "Minimalny próg score dla listy kandydatów.",
  "drafts.title":
    "Drafty: generowanie wariantów treści na podstawie wybranego kandydata i kanałów.",
  "drafts.candidate": "Kandydat (temat), z którego zostaną wygenerowane drafty.",
  "queue.title":
    "Kolejka publikacji: wpisy gotowe lub oczekujące na publikację do kanałów.",
  "queue.account": "Konto docelowe kanału, którego użyje publikacja.",
  "audit.title":
    "Audyt: historia operacji modułu (generowanie draftów, testy integracji, publikacje).",
  "config.editStrategy": "Wybór strategii, którą teraz edytujesz.",
  "config.activeStrategy": "Strategia aktualnie aktywna w runtime modułu.",
  "config.strategyName": "Nazwa strategii widoczna w selectorze i operacjach.",
  "config.discoveryMode":
    "stub = dane przykładowe, hybrid = live z fallbackiem, live = tylko dane zewnętrzne.",
  "config.cacheTtl": "Jak długo (sekundy) cache kandydatów jest uznawany za świeży.",
  "config.limit": "Maksymalna liczba kandydatów zwracanych po filtrach.",
  "config.minScore": "Domyślny próg score strategii używany przy pobieraniu kandydatów.",
  "config.rss":
    "Lista feedów RSS do monitorowania (jeden URL na linię), wykorzystywana w discovery.",
  "config.topicKeywords":
    "Frazy tematyczne używane do dodatkowego filtrowania kandydatów po tytule/opisie/URL.",
  "config.channels": "Kanały, dla których generowane są drafty i ustawienia domyślne.",
  "config.languages": "Języki, w których moduł ma generować warianty draftów.",
  "config.save": "Zapisuje konfigurację aktywnej strategii.",
  "config.updateStrategy": "Zapisuje zmiany w aktualnie edytowanej strategii.",
  "config.newStrategy": "Tworzy nową strategię (kopiując bazę i nadpisane pola).",
  "config.duplicate": "Tworzy kopię wybranej strategii.",
  "config.activate": "Ustawia wybraną strategię jako aktywną.",
  "config.delete": "Usuwa wybraną strategię (jeśli nie jest jedyną).",
  "config.refreshNow": "Wymusza natychmiastowe odświeżenie kandydatów z backendu.",
  "config.restore": "Pobiera konfigurację ponownie z backendu i nadpisuje formularz.",
  "integrations.loading": "Stan integracji i kluczy API wymaganych przez kanały/źródła.",
  "integrations.keyRequired": "Integracja wymaga prywatnego klucza/tokenu.",
  "integrations.public": "Integracja działa na publicznym API bez klucza.",
  "integrations.key": "Nazwa zmiennej środowiskowej z sekretem.",
  "integrations.test": "Uruchamia test połączenia dla danej integracji.",
  "accounts.title":
    "Konta kanałów publikacji. Tu dodajesz konta i ustawiasz konto domyślne per kanał.",
  "accounts.displayName": "Czytelna nazwa konta widoczna w UI.",
  "accounts.target":
    "Cel publikacji zależny od kanału (np. repo, publication, subreddit, profil).",
  "accounts.add": "Dodaje nowe konto do kanału.",
  "accounts.setDefault": "Ustawia konto jako domyślne dla kanału.",
  "accounts.test": "Sprawdza konfigurację i dostępność konta.",
  "accounts.delete": "Usuwa konto z kanału.",
};

function HelpBadge({ tip }: Readonly<{ tip: string | null }>) {
  if (!tip) {
    return null;
  }
  return (
    <span
      title={tip}
      className="inline-flex h-6 w-6 shrink-0 cursor-help items-center justify-center rounded-full border border-zinc-500/80 bg-zinc-900/70 text-[11px] font-semibold leading-none text-zinc-200 transition hover:border-cyan-400/80 hover:text-cyan-200"
      aria-label={tip}
    >
      ?
    </span>
  );
}

function TabIcon({ tab }: Readonly<{ tab: Tab }>) {
  if (tab === "radar") {
    return (
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="h-4 w-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M12 18a6 6 0 1 0-6-6" />
        <path d="M12 14a2 2 0 1 0-2-2" />
        <path d="M12 2v2" />
      </svg>
    );
  }
  if (tab === "config") {
    return (
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="h-4 w-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />
        <path d="M19.4 15a1 1 0 0 0 .2 1.1l.1.1a1.5 1.5 0 1 1-2.1 2.1l-.1-.1a1 1 0 0 0-1.1-.2 1 1 0 0 0-.6.9V19a1.5 1.5 0 0 1-3 0v-.1a1 1 0 0 0-.6-.9 1 1 0 0 0-1.1.2l-.1.1a1.5 1.5 0 1 1-2.1-2.1l.1-.1a1 1 0 0 0 .2-1.1 1 1 0 0 0-.9-.6H5a1.5 1.5 0 0 1 0-3h.1a1 1 0 0 0 .9-.6 1 1 0 0 0-.2-1.1l-.1-.1a1.5 1.5 0 1 1 2.1-2.1l.1.1a1 1 0 0 0 1.1.2 1 1 0 0 0 .6-.9V5a1.5 1.5 0 0 1 3 0v.1a1 1 0 0 0 .6.9 1 1 0 0 0 1.1-.2l.1-.1a1.5 1.5 0 1 1 2.1 2.1l-.1.1a1 1 0 0 0-.2 1.1 1 1 0 0 0 .9.6H19a1.5 1.5 0 0 1 0 3h-.1a1 1 0 0 0-.9.6Z" />
      </svg>
    );
  }
  if (tab === "monitoring") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
      </svg>
    );
  }
  if (tab === "sources") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
        <polyline points="9 22 9 12 15 12 15 22" />
      </svg>
    );
  }
  if (tab === "keywords") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20 11V5a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h8" />
        <path d="M17 17l2 2 4-4" />
      </svg>
    );
  }
  if (tab === "campaigns") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
      </svg>
    );
  }
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 5h16" />
      <path d="M4 12h16" />
      <path d="M4 19h16" />
      <circle cx="8" cy="5" r="1" fill="currentColor" stroke="none" />
      <circle cx="16" cy="12" r="1" fill="currentColor" stroke="none" />
      <circle cx="10" cy="19" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}

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
  const [queueChannelFilter, setQueueChannelFilter] = useState<"all" | PublishChannel>("all");
  const [queueStatusFilter, setQueueStatusFilter] = useState<
    "all" | QueueItem["status"]
  >("all");
  const [queueOutcomeFilter, setQueueOutcomeFilter] = useState<"all" | LogOutcome>("all");
  const [audit, setAudit] = useState<AuditItem[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [auditCategoryFilter, setAuditCategoryFilter] = useState<
    "all" | "queue" | "draft" | "integration" | "config" | "strategy" | "channel"
  >("all");
  const [auditStatusFilter, setAuditStatusFilter] = useState<"all" | string>("all");
  const [auditOutcomeFilter, setAuditOutcomeFilter] = useState<"all" | LogOutcome>("all");

  const [strategies, setStrategies] = useState<StrategyConfig[]>([]);
  const [activeStrategyId, setActiveStrategyId] = useState<string>("");
  const [selectedStrategyId, setSelectedStrategyId] = useState<string>("");
  const [configForm, setConfigForm] = useState<StrategyConfig>(DEFAULT_FORM);
  const [configLoading, setConfigLoading] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);
  const [strategyName, setStrategyName] = useState("");

  const [integrations, setIntegrations] = useState<IntegrationDescriptor[]>([]);
  const [integrationLoading, setIntegrationLoading] = useState(false);
  const [integrationError, setIntegrationError] = useState<string | null>(null);
  const [integrationTests, setIntegrationTests] = useState<Record<string, string>>({});
  const [channelDescriptors, setChannelDescriptors] = useState<ChannelDescriptor[]>([]);
  const [accountsByChannel, setAccountsByChannel] = useState<Partial<Record<PublishChannel, ChannelAccount[]>>>({});
  const [selectedAccountByChannel, setSelectedAccountByChannel] = useState<
    Partial<Record<PublishChannel, string>>
  >({});
  const [accountDraftDisplayNameByChannel, setAccountDraftDisplayNameByChannel] = useState<
    Partial<Record<PublishChannel, string>>
  >({});
  const [accountDraftTargetByChannel, setAccountDraftTargetByChannel] = useState<
    Partial<Record<PublishChannel, string>>
  >({});

  const [monitoringSummary, setMonitoringSummary] = useState<BrandMonitoringSummary | null>(null);
  const [monitoringResults, setMonitoringResults] = useState<BrandSearchResult[]>([]);
  const [monitoringResultsVisible, setMonitoringResultsVisible] = useState(20);
  const [monitoringLoading, setMonitoringLoading] = useState(false);
  const [monitoringError, setMonitoringError] = useState<string | null>(null);
  const [keywords, setKeywords] = useState<BrandKeyword[]>([]);
  const [keywordsLoading, setKeywordsLoading] = useState(false);
  const [keywordsError, setKeywordsError] = useState<string | null>(null);
  const [newKeywordPhrase, setNewKeywordPhrase] = useState("");
  const [newKeywordType, setNewKeywordType] = useState<KeywordType>("brand_core");
  const [baseSources, setBaseSources] = useState<BrandBaseSource[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [newSourceName, setNewSourceName] = useState("");
  const [newSourceUrl, setNewSourceUrl] = useState("");
  const [newSourceChannel, setNewSourceChannel] = useState<PublishChannel>("blog");
  const [campaigns, setCampaigns] = useState<BrandCampaign[]>([]);
  const [campaignsLoading, setCampaignsLoading] = useState(false);
  const [campaignsError, setCampaignsError] = useState<string | null>(null);
  const [newCampaignName, setNewCampaignName] = useState("");
  const [newCampaignChannels, setNewCampaignChannels] = useState<PublishChannel[]>(["x"]);

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

  const help = useCallback(
    (key: string): string | null => {
      if (lang !== "pl") {
        return null;
      }
      return PL_HELP[key] ?? null;
    },
    [lang]
  );

  const normalizeRssUrls = useCallback((raw: string): string[] => {
    return raw
      .split("\n")
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
  }, []);

  const normalizeTopicKeywords = useCallback((raw: string): string[] => {
    return raw
      .split("\n")
      .map((item) => item.trim())
      .filter((item, index, array) => item.length > 0 && array.indexOf(item) === index);
  }, []);

  const normalizeStrategy = useCallback((raw: StrategyConfig): StrategyConfig => {
    return {
      ...raw,
      rss_urls: Array.isArray(raw.rss_urls) ? raw.rss_urls : [],
      topic_keywords: Array.isArray(raw.topic_keywords) ? raw.topic_keywords : [],
      default_accounts: raw.default_accounts ?? {},
    };
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
      setSelectedStrategyId(configPayload.active_strategy_id);
      const active = normalizeStrategy(configPayload.active_strategy);
      setConfigForm(active);
      setStrategies(strategiesPayload.items.map((item) => normalizeStrategy(item)));
      setStrategyName(active.name);
      setMinScore(active.min_score);
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setConfigLoading(false);
    }
  }, [normalizeStrategy]);

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

  const loadChannels = useCallback(async () => {
    setIntegrationLoading(true);
    setIntegrationError(null);
    try {
      const response = await fetch("/api/v1/brand-studio/channels", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = (await response.json()) as ChannelsResponse;
      setChannelDescriptors(payload.items);

      const accountPairs = await Promise.all(
        payload.items.map(async (channel) => {
          const accountsResponse = await fetch(
            `/api/v1/brand-studio/channels/${channel.id}/accounts`,
            { cache: "no-store" }
          );
          if (!accountsResponse.ok) {
            throw new Error(`HTTP ${accountsResponse.status}`);
          }
          const accountsPayload = (await accountsResponse.json()) as ChannelAccountsResponse;
          return [channel.id, accountsPayload.items] as const;
        })
      );

      const nextAccountsByChannel: Partial<Record<PublishChannel, ChannelAccount[]>> = {};
      const nextSelectedByChannel: Partial<Record<PublishChannel, string>> = {};
      for (const [channelId, accounts] of accountPairs) {
        nextAccountsByChannel[channelId] = accounts;
        const selected =
          accounts.find((item) => item.is_default)?.account_id ?? accounts[0]?.account_id;
        if (selected) {
          nextSelectedByChannel[channelId] = selected;
        }
      }
      setAccountsByChannel(nextAccountsByChannel);
      setSelectedAccountByChannel((previous) => {
        const validatedPrevious: Partial<Record<PublishChannel, string>> = {};
        for (const [channelId, accounts] of Object.entries(
          nextAccountsByChannel
        ) as [PublishChannel, ChannelAccount[]][]) {
          const prevSelected = previous[channelId];
          if (
            prevSelected &&
            accounts.some((account) => account.account_id === prevSelected)
          ) {
            validatedPrevious[channelId] = prevSelected;
          }
        }
        return { ...nextSelectedByChannel, ...validatedPrevious };
      });
    } catch (err) {
      setIntegrationError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setIntegrationLoading(false);
    }
  }, []);

  const loadMonitoring = useCallback(async () => {
    setMonitoringLoading(true);
    setMonitoringError(null);
    try {
      const [summaryResp, resultsResp] = await Promise.all([
        fetch("/api/v1/brand-studio/monitoring/summary", { cache: "no-store" }),
        fetch("/api/v1/brand-studio/monitoring/results", { cache: "no-store" }),
      ]);
      if (summaryResp.ok) {
        setMonitoringSummary((await summaryResp.json()) as BrandMonitoringSummary);
      }
      if (resultsResp.ok) {
        const payload = (await resultsResp.json()) as { count: number; items: BrandSearchResult[] };
        setMonitoringResults(payload.items);
      }
    } catch (err) {
      setMonitoringError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setMonitoringLoading(false);
    }
  }, []);

  const loadKeywords = useCallback(async () => {
    setKeywordsLoading(true);
    try {
      const resp = await fetch("/api/v1/brand-studio/monitoring/keywords", { cache: "no-store" });
      if (resp.ok) {
        const payload = (await resp.json()) as { count: number; items: BrandKeyword[] };
        setKeywords(payload.items);
      }
    } finally {
      setKeywordsLoading(false);
    }
  }, []);

  const loadBaseSources = useCallback(async () => {
    setSourcesLoading(true);
    try {
      const resp = await fetch("/api/v1/brand-studio/monitoring/sources", { cache: "no-store" });
      if (resp.ok) {
        const payload = (await resp.json()) as { count: number; items: BrandBaseSource[] };
        setBaseSources(payload.items);
      }
    } finally {
      setSourcesLoading(false);
    }
  }, []);

  const loadCampaigns = useCallback(async () => {
    setCampaignsLoading(true);
    setCampaignsError(null);
    try {
      const resp = await fetch("/api/v1/brand-studio/campaigns", { cache: "no-store" });
      if (resp.ok) {
        const payload = (await resp.json()) as { count: number; items: BrandCampaign[] };
        setCampaigns(payload.items);
      }
    } catch (err) {
      setCampaignsError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setCampaignsLoading(false);
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
    void loadChannels();
    void loadMonitoring();
    void loadKeywords();
    void loadBaseSources();
    void loadCampaigns();
  }, [loadQueue, loadAudit, loadConfig, loadIntegrations, loadChannels, loadMonitoring, loadKeywords, loadBaseSources, loadCampaigns]);

  const stats = useMemo(() => {
    if (!items.length) {
      return { count: 0, topScore: 0, freshest: "-" };
    }
    const topScore = Math.max(...items.map((item) => item.score));
    const freshestMinutes = Math.min(...items.map((item) => item.age_minutes));
    return { count: items.length, topScore, freshest: `${freshestMinutes}m` };
  }, [items]);

  const queueOutcome = useCallback((status: QueueItem["status"]): LogOutcome => {
    if (status === "failed") {
      return "error";
    }
    if (status === "published") {
      return "success";
    }
    return "warning";
  }, []);

  const auditOutcome = useCallback((status: string): LogOutcome => {
    const normalized = status.toLowerCase();
    if (normalized === "failed") {
      return "error";
    }
    if (normalized === "ok" || normalized === "published") {
      return "success";
    }
    return "warning";
  }, []);

  const outcomeClass = useCallback((outcome: LogOutcome): string => {
    if (outcome === "error") {
      return "border-rose-500/40 bg-rose-500/10 text-rose-200";
    }
    if (outcome === "success") {
      return "border-emerald-500/40 bg-emerald-500/10 text-emerald-200";
    }
    return "border-amber-500/40 bg-amber-500/10 text-amber-200";
  }, []);

  const filteredQueue = useMemo(() => {
    return queue.filter((item) => {
      if (queueChannelFilter !== "all" && item.target_channel !== queueChannelFilter) {
        return false;
      }
      if (queueStatusFilter !== "all" && item.status !== queueStatusFilter) {
        return false;
      }
      if (queueOutcomeFilter !== "all" && queueOutcome(item.status) !== queueOutcomeFilter) {
        return false;
      }
      return true;
    });
  }, [queue, queueChannelFilter, queueOutcome, queueOutcomeFilter, queueStatusFilter]);

  const filteredAudit = useMemo(() => {
    return audit.filter((entry) => {
      if (auditCategoryFilter !== "all" && !entry.action.startsWith(`${auditCategoryFilter}.`)) {
        return false;
      }
      if (auditStatusFilter !== "all" && entry.status !== auditStatusFilter) {
        return false;
      }
      if (auditOutcomeFilter !== "all" && auditOutcome(entry.status) !== auditOutcomeFilter) {
        return false;
      }
      return true;
    });
  }, [audit, auditCategoryFilter, auditOutcome, auditOutcomeFilter, auditStatusFilter]);

  const generateDraft = useCallback(async () => {
    if (!selectedCandidateId) {
      return;
    }
    setDraftLoading(true);
    setDraftError(null);
    try {
      const selectedChannels =
        channel === "all"
          ? configForm.active_channels
          : ([channel] as const);
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
  }, [apiLang, channel, configForm.active_channels, loadAudit, selectedCandidateId]);

  const queueVariant = useCallback(
    async (variant: DraftVariant) => {
      if (!draft) {
        return;
      }
      setQueueLoading(true);
      setQueueError(null);
      try {
        const selectedAccountId = selectedAccountByChannel[variant.channel] ?? null;
        const response = await fetch(`/api/v1/brand-studio/drafts/${draft.draft_id}/queue`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Authenticated-User": "local-user",
          },
          body: JSON.stringify({
            target_channel: variant.channel,
            target_language: variant.language,
            account_id: selectedAccountId,
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
    [draft, loadAudit, loadQueue, selectedAccountByChannel]
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
    if (selectedStrategyId !== activeStrategyId) {
      setConfigError("Save updates only the active strategy. Activate selected strategy first.");
      return;
    }
    setConfigLoading(true);
    setConfigError(null);
    try {
      const payload = {
        discovery_mode: configForm.discovery_mode,
        rss_urls: configForm.rss_urls,
        topic_keywords: configForm.topic_keywords,
        cache_ttl_seconds: configForm.cache_ttl_seconds,
        min_score: configForm.min_score,
        limit: configForm.limit,
        active_channels: configForm.active_channels,
        draft_languages: configForm.draft_languages,
        default_accounts: configForm.default_accounts,
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
  }, [activeStrategyId, configForm, loadAudit, loadCandidates, loadConfig, selectedStrategyId]);

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
          topic_keywords: configForm.topic_keywords,
          cache_ttl_seconds: configForm.cache_ttl_seconds,
          min_score: configForm.min_score,
          limit: configForm.limit,
          active_channels: configForm.active_channels,
          draft_languages: configForm.draft_languages,
          default_accounts: configForm.default_accounts,
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

  const createChannelAccount = useCallback(
    async (channelId: PublishChannel) => {
      const displayName = (accountDraftDisplayNameByChannel[channelId] ?? "").trim();
      if (!displayName) {
        setIntegrationError(t("accounts.validationName"));
        return;
      }
      setIntegrationLoading(true);
      setIntegrationError(null);
      try {
        const response = await fetch(`/api/v1/brand-studio/channels/${channelId}/accounts`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Authenticated-User": "local-user",
          },
          body: JSON.stringify({
            display_name: displayName,
            target: (accountDraftTargetByChannel[channelId] ?? "").trim() || null,
            is_default: !accountsByChannel[channelId]?.length,
            enabled: true,
          }),
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        setAccountDraftDisplayNameByChannel((previous) => ({ ...previous, [channelId]: "" }));
        setAccountDraftTargetByChannel((previous) => ({ ...previous, [channelId]: "" }));
        await loadChannels();
        await loadAudit();
      } catch (err) {
        setIntegrationError(err instanceof Error ? err.message : "unknown_error");
      } finally {
        setIntegrationLoading(false);
      }
    },
    [
      accountDraftDisplayNameByChannel,
      accountDraftTargetByChannel,
      accountsByChannel,
      loadAudit,
      loadChannels,
      t,
    ]
  );

  const activateChannelAccount = useCallback(
    async (channelId: PublishChannel, accountId: string) => {
      setIntegrationLoading(true);
      setIntegrationError(null);
      try {
        const response = await fetch(
          `/api/v1/brand-studio/channels/${channelId}/accounts/${accountId}/activate`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Authenticated-User": "local-user",
            },
          }
        );
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        await loadChannels();
        await loadAudit();
      } catch (err) {
        setIntegrationError(err instanceof Error ? err.message : "unknown_error");
      } finally {
        setIntegrationLoading(false);
      }
    },
    [loadAudit, loadChannels]
  );

  const testChannelAccount = useCallback(
    async (channelId: PublishChannel, accountId: string) => {
      setIntegrationLoading(true);
      setIntegrationError(null);
      try {
        const response = await fetch(
          `/api/v1/brand-studio/channels/${channelId}/accounts/${accountId}/test`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Authenticated-User": "local-user",
            },
          }
        );
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const payload = (await response.json()) as {
          account_id: string;
          status: string;
          message: string;
        };
        setIntegrationTests((previous) => ({
          ...previous,
          [`${channelId}:${accountId}`]: `${payload.status}: ${payload.message}`,
        }));
        await loadChannels();
        await loadAudit();
      } catch (err) {
        setIntegrationError(err instanceof Error ? err.message : "unknown_error");
      } finally {
        setIntegrationLoading(false);
      }
    },
    [loadAudit, loadChannels]
  );

  const deleteChannelAccount = useCallback(
    async (channelId: PublishChannel, accountId: string) => {
      setIntegrationLoading(true);
      setIntegrationError(null);
      try {
        const response = await fetch(
          `/api/v1/brand-studio/channels/${channelId}/accounts/${accountId}`,
          {
            method: "DELETE",
            headers: {
              "Content-Type": "application/json",
              "X-Authenticated-User": "local-user",
            },
          }
        );
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        await loadChannels();
        await loadAudit();
      } catch (err) {
        setIntegrationError(err instanceof Error ? err.message : "unknown_error");
      } finally {
        setIntegrationLoading(false);
      }
    },
    [loadAudit, loadChannels]
  );

  const addKeyword = useCallback(async () => {
    const phrase = newKeywordPhrase.trim();
    if (!phrase) return;
    setKeywordsLoading(true);
    setKeywordsError(null);
    try {
      const resp = await fetch("/api/v1/brand-studio/monitoring/keywords", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Authenticated-User": "local-user" },
        body: JSON.stringify({ phrase, keyword_type: newKeywordType }),
      });
      if (!resp.ok) {
        throw new Error(await extractErrorMessage(resp));
      }
      setNewKeywordPhrase("");
      await loadKeywords();
      await loadAudit();
    } catch (err) {
      setKeywordsError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setKeywordsLoading(false);
    }
  }, [newKeywordPhrase, newKeywordType, loadKeywords, loadAudit]);

  const deleteKeyword = useCallback(
    async (keywordId: string) => {
      setKeywordsLoading(true);
      try {
        await fetch(`/api/v1/brand-studio/monitoring/keywords/${keywordId}`, {
          method: "DELETE",
          headers: { "X-Authenticated-User": "local-user" },
        });
        await loadKeywords();
        await loadAudit();
      } finally {
        setKeywordsLoading(false);
      }
    },
    [loadKeywords, loadAudit]
  );

  const runMonitoringScan = useCallback(async () => {
    setMonitoringLoading(true);
    setMonitoringError(null);
    try {
      const resp = await fetch("/api/v1/brand-studio/monitoring/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Authenticated-User": "local-user" },
        body: JSON.stringify({}),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      await loadMonitoring();
      await loadAudit();
    } catch (err) {
      setMonitoringError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setMonitoringLoading(false);
    }
  }, [loadMonitoring, loadAudit]);

  const addBaseSource = useCallback(async () => {
    const name = newSourceName.trim();
    const url = newSourceUrl.trim();
    if (!name || !url) return;
    setSourcesLoading(true);
    setSourcesError(null);
    try {
      const resp = await fetch("/api/v1/brand-studio/monitoring/sources", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Authenticated-User": "local-user" },
        body: JSON.stringify({ name, base_url: url, channel: newSourceChannel }),
      });
      if (!resp.ok) {
        throw new Error(await extractErrorMessage(resp));
      }
      setNewSourceName("");
      setNewSourceUrl("");
      await loadBaseSources();
      await loadAudit();
    } catch (err) {
      setSourcesError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setSourcesLoading(false);
    }
  }, [newSourceName, newSourceUrl, newSourceChannel, loadBaseSources, loadAudit]);

  const deleteBaseSource = useCallback(
    async (sourceId: string) => {
      setSourcesLoading(true);
      try {
        await fetch(`/api/v1/brand-studio/monitoring/sources/${sourceId}`, {
          method: "DELETE",
          headers: { "X-Authenticated-User": "local-user" },
        });
        await loadBaseSources();
        await loadAudit();
      } finally {
        setSourcesLoading(false);
      }
    },
    [loadBaseSources, loadAudit]
  );

  const createCampaign = useCallback(async () => {
    const name = newCampaignName.trim();
    if (!name || !newCampaignChannels.length) return;
    setCampaignsLoading(true);
    setCampaignsError(null);
    try {
      const resp = await fetch("/api/v1/brand-studio/campaigns", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Authenticated-User": "local-user" },
        body: JSON.stringify({ name, channels: newCampaignChannels }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setNewCampaignName("");
      await loadCampaigns();
      await loadAudit();
    } catch (err) {
      setCampaignsError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setCampaignsLoading(false);
    }
  }, [newCampaignName, newCampaignChannels, loadCampaigns, loadAudit]);

  const runCampaign = useCallback(
    async (campaignId: string) => {
      setCampaignsLoading(true);
      setCampaignsError(null);
      try {
        const resp = await fetch(`/api/v1/brand-studio/campaigns/${campaignId}/run`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Authenticated-User": "local-user" },
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        await loadCampaigns();
        await loadAudit();
      } catch (err) {
        setCampaignsError(err instanceof Error ? err.message : "unknown_error");
      } finally {
        setCampaignsLoading(false);
      }
    },
    [loadCampaigns, loadAudit]
  );

  const createCampaignFromResult = useCallback(async (resultId: string, resultTitle: string) => {
    setCampaignsLoading(true);
    setCampaignsError(null);
    try {
      const baseName = resultTitle.trim() || "Campaign from monitoring";
      let name = baseName;
      if (baseName.length > MAX_CAMPAIGN_NAME_LENGTH) {
        const ellipsis = "...";
        name = baseName.slice(0, MAX_CAMPAIGN_NAME_LENGTH - ellipsis.length) + ellipsis;
      }
      const resp = await fetch("/api/v1/brand-studio/campaigns", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Authenticated-User": "local-user" },
        body: JSON.stringify({
          name,
          channels: ["x"],
          linked_result_ids: [resultId],
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      await loadCampaigns();
      setTab("campaigns");
    } catch (err) {
      setCampaignsError(err instanceof Error ? err.message : "unknown_error");
    } finally {
      setCampaignsLoading(false);
    }
  }, [loadCampaigns, setTab]);

  const toggleChannel = (value: PublishChannel) => {
    setConfigError(null);
    setConfigForm((previous) => {
      const exists = previous.active_channels.includes(value);
      if (exists && previous.active_channels.length === 1) {
        setConfigError("At least one channel must remain selected.");
        return previous;
      }
      const next = exists
        ? previous.active_channels.filter((item) => item !== value)
        : [...previous.active_channels, value];
      return { ...previous, active_channels: next };
    });
  };

  const toggleLanguage = (value: "pl" | "en") => {
    setConfigError(null);
    setConfigForm((previous) => {
      const exists = previous.draft_languages.includes(value);
      if (exists && previous.draft_languages.length === 1) {
        setConfigError("At least one language must remain selected.");
        return previous;
      }
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

      <section className="flex flex-wrap gap-2 border-b border-white/10">
        {(["radar", "monitoring", "sources", "keywords", "campaigns", "config", "integrations"] as const).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => setTab(value)}
            className={`inline-flex items-center gap-2 rounded-t-xl rounded-b-none px-4 py-3 text-sm font-medium transition ${
              tab === value
                ? "border-b-2 border-emerald-400 bg-emerald-500/10 text-emerald-300"
                : "text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
            }`}
          >
            <TabIcon tab={value} />
            {t(`tabs.${value}`)}
            <HelpBadge tip={help(`tabs.${value}`)} />
          </button>
        ))}
      </section>

      {tab === "radar" ? (
        <>
          <section className="grid gap-3 md:grid-cols-4">
            <div className="glass-panel rounded-2xl border border-cyan-500/20 p-4">
              <p className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                {t("stats.count")}
                <HelpBadge tip={help("stats.count")} />
              </p>
              <p className="text-2xl font-semibold text-white">{stats.count}</p>
            </div>
            <div className="glass-panel rounded-2xl border border-cyan-500/20 p-4">
              <p className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                {t("stats.topScore")}
                <HelpBadge tip={help("stats.topScore")} />
              </p>
              <p className="text-2xl font-semibold text-white">{stats.topScore.toFixed(2)}</p>
            </div>
            <div className="glass-panel rounded-2xl border border-cyan-500/20 p-4">
              <p className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                {t("stats.freshest")}
                <HelpBadge tip={help("stats.freshest")} />
              </p>
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
                <span className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                  {t("filters.language")}
                  <HelpBadge tip={help("filters.language")} />
                </span>
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
                <span className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                  {t("filters.channel")}
                  <HelpBadge tip={help("filters.channel")} />
                </span>
                <select
                  value={channel}
                  onChange={(event) => setChannel(event.target.value as Channel)}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                >
                  <option value="all">{t("filters.allChannels")}</option>
                  {CHANNELS.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-1">
                <span className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                  {t("filters.minScore")}: {minScore.toFixed(2)}
                  <HelpBadge tip={help("filters.minScore")} />
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

          <section className="space-y-4">
            <div className="glass-panel space-y-3 rounded-2xl border border-emerald-500/20 p-4">
              <h2 className="inline-flex items-center gap-2 text-lg font-medium text-emerald-100">
                {t("drafts.title")}
                <HelpBadge tip={help("drafts.title")} />
              </h2>
              <div className="grid gap-3 md:grid-cols-[1fr_auto] md:items-end">
                <label className="space-y-1">
                  <span className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                    {t("drafts.candidate")}
                    <HelpBadge tip={help("drafts.candidate")} />
                  </span>
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
              </div>
              {draftError ? <p className="text-rose-300">{draftError}</p> : null}
              {draft ? (
                <div className="max-h-[340px] space-y-2 overflow-y-auto pr-1">
                  {draft.variants.map((variant, index) => (
                    <article key={`${variant.channel}-${variant.language}-${index}`} className="rounded-lg border border-zinc-800 p-3">
                      <p className="text-xs uppercase text-zinc-400">
                        {variant.channel} / {variant.language}
                      </p>
                      {accountsByChannel[variant.channel]?.length ? (
                        <label className="mt-2 block space-y-1">
                          <span className="inline-flex items-center gap-1 text-[11px] uppercase text-zinc-500">
                            {t("queue.account")}
                            <HelpBadge tip={help("queue.account")} />
                          </span>
                          <select
                            value={
                              selectedAccountByChannel[variant.channel] ??
                              accountsByChannel[variant.channel]?.find((item) => item.is_default)?.account_id ??
                              accountsByChannel[variant.channel]?.[0]?.account_id ??
                              ""
                            }
                            onChange={(event) =>
                              setSelectedAccountByChannel((previous) => ({
                                ...previous,
                                [variant.channel]: event.target.value,
                              }))
                            }
                            className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-2 py-1 text-xs text-zinc-100"
                          >
                            {accountsByChannel[variant.channel]?.map((account) => (
                              <option key={account.account_id} value={account.account_id}>
                                {account.display_name}
                              </option>
                            ))}
                          </select>
                        </label>
                      ) : null}
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

            <div className="grid gap-4 lg:grid-cols-2">
              <div className="glass-panel space-y-3 rounded-2xl border border-violet-500/20 p-4">
              <h2 className="inline-flex items-center gap-2 text-lg font-medium text-violet-100">
                {t("queue.title")}
                <HelpBadge tip={help("queue.title")} />
              </h2>
              {queueLoading ? <p className="text-zinc-400">{t("queue.loading")}</p> : null}
              {queueError ? <p className="text-rose-300">{queueError}</p> : null}
              {!queueLoading && !queue.length ? <p className="text-zinc-400">{t("queue.empty")}</p> : null}
              <div className="grid gap-2 md:grid-cols-3">
                <label className="space-y-1">
                  <span className="text-[11px] uppercase text-zinc-500">
                    {lang === "pl" ? "Filtr kanału" : "Channel filter"}
                  </span>
                  <select
                    value={queueChannelFilter}
                    onChange={(event) =>
                      setQueueChannelFilter(event.target.value as "all" | PublishChannel)
                    }
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-2 py-1 text-xs text-zinc-100"
                  >
                    <option value="all">{lang === "pl" ? "Wszystkie kanały" : "All channels"}</option>
                    {CHANNELS.map((channelId) => (
                      <option key={channelId} value={channelId}>
                        {channelId}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="space-y-1">
                  <span className="text-[11px] uppercase text-zinc-500">
                    {lang === "pl" ? "Filtr statusu" : "Status filter"}
                  </span>
                  <select
                    value={queueStatusFilter}
                    onChange={(event) =>
                      setQueueStatusFilter(event.target.value as "all" | QueueItem["status"])
                    }
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-2 py-1 text-xs text-zinc-100"
                  >
                    <option value="all">{lang === "pl" ? "Wszystkie statusy" : "All statuses"}</option>
                    <option value="queued">queued</option>
                    <option value="published">published</option>
                    <option value="failed">failed</option>
                    <option value="draft">draft</option>
                    <option value="ready">ready</option>
                    <option value="cancelled">cancelled</option>
                  </select>
                </label>
                <label className="space-y-1">
                  <span className="text-[11px] uppercase text-zinc-500">
                    {lang === "pl" ? "Wynik" : "Outcome"}
                  </span>
                  <select
                    value={queueOutcomeFilter}
                    onChange={(event) =>
                      setQueueOutcomeFilter(event.target.value as "all" | LogOutcome)
                    }
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-2 py-1 text-xs text-zinc-100"
                  >
                    <option value="all">{lang === "pl" ? "Wszystkie" : "All"}</option>
                    <option value="success">{lang === "pl" ? "Sukces" : "Success"}</option>
                    <option value="warning">{lang === "pl" ? "Warning" : "Warning"}</option>
                    <option value="error">{lang === "pl" ? "Błąd" : "Error"}</option>
                  </select>
                </label>
              </div>
              <p className="text-[11px] text-zinc-500">
                Dane ładowane przy wejściu na ekran i po akcjach (generowanie/kolejkowanie/publikacja).
              </p>
              <div
                className="space-y-2 pr-2"
                style={{
                  maxHeight: "690px",
                  overflowY: "scroll",
                  scrollbarGutter: "stable",
                  overscrollBehavior: "contain",
                }}
              >
                {filteredQueue.map((item) => (
                  <article key={item.item_id} className="min-h-[44px] rounded-lg border border-zinc-800 p-3">
                    <div className="flex flex-wrap items-center gap-3 text-xs">
                      <span
                        className={`rounded border px-2 py-1 uppercase ${outcomeClass(
                          queueOutcome(item.status)
                        )}`}
                      >
                        {item.target_channel} / {item.status}
                      </span>
                      {item.account_display_name ? (
                        <span className="text-zinc-400">
                          {t("queue.account")}: {item.account_display_name}
                        </span>
                      ) : null}
                      <span className="text-zinc-500">{item.item_id}</span>
                      <button
                        type="button"
                        onClick={() => void publishNow(item.item_id)}
                        disabled={item.status === "published" || queueLoading}
                        className="rounded-lg border border-violet-500/30 px-3 py-1 text-xs text-violet-100 hover:border-violet-400 disabled:opacity-50"
                      >
                        {item.status === "published" ? t("queue.published") : t("queue.publishNow")}
                      </button>
                    </div>
                  </article>
                ))}
              </div>
              </div>

              <div className="glass-panel space-y-3 rounded-2xl border border-cyan-500/20 p-4">
              <h2 className="inline-flex items-center gap-2 text-lg font-medium text-cyan-100">
                {t("audit.title")}
                <HelpBadge tip={help("audit.title")} />
              </h2>
              {auditLoading ? <p className="text-zinc-400">{t("audit.loading")}</p> : null}
              {auditError ? <p className="text-rose-300">{auditError}</p> : null}
              {!auditLoading && !audit.length ? <p className="text-zinc-400">{t("audit.empty")}</p> : null}
              <div className="grid gap-2 md:grid-cols-3">
                <label className="space-y-1">
                  <span className="text-[11px] uppercase text-zinc-500">
                    {lang === "pl" ? "Filtr API/akcji" : "API/action filter"}
                  </span>
                  <select
                    value={auditCategoryFilter}
                    onChange={(event) =>
                      setAuditCategoryFilter(
                        event.target.value as
                          | "all"
                          | "queue"
                          | "draft"
                          | "integration"
                          | "config"
                          | "strategy"
                          | "channel"
                      )
                    }
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-2 py-1 text-xs text-zinc-100"
                  >
                    <option value="all">{lang === "pl" ? "Wszystkie akcje" : "All actions"}</option>
                    <option value="queue">queue.*</option>
                    <option value="draft">draft.*</option>
                    <option value="integration">integration.*</option>
                    <option value="config">config.*</option>
                    <option value="strategy">strategy.*</option>
                    <option value="channel">channel.*</option>
                  </select>
                </label>
                <label className="space-y-1">
                  <span className="text-[11px] uppercase text-zinc-500">
                    {lang === "pl" ? "Filtr statusu" : "Status filter"}
                  </span>
                  <select
                    value={auditStatusFilter}
                    onChange={(event) => setAuditStatusFilter(event.target.value)}
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-2 py-1 text-xs text-zinc-100"
                  >
                    <option value="all">{lang === "pl" ? "Wszystkie statusy" : "All statuses"}</option>
                    <option value="ok">ok</option>
                    <option value="queued">queued</option>
                    <option value="published">published</option>
                    <option value="failed">failed</option>
                    <option value="manual">manual</option>
                  </select>
                </label>
                <label className="space-y-1">
                  <span className="text-[11px] uppercase text-zinc-500">
                    {lang === "pl" ? "Wynik" : "Outcome"}
                  </span>
                  <select
                    value={auditOutcomeFilter}
                    onChange={(event) =>
                      setAuditOutcomeFilter(event.target.value as "all" | LogOutcome)
                    }
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-2 py-1 text-xs text-zinc-100"
                  >
                    <option value="all">{lang === "pl" ? "Wszystkie" : "All"}</option>
                    <option value="success">{lang === "pl" ? "Sukces" : "Success"}</option>
                    <option value="warning">{lang === "pl" ? "Warning" : "Warning"}</option>
                    <option value="error">{lang === "pl" ? "Błąd" : "Error"}</option>
                  </select>
                </label>
              </div>
              <p className="text-[11px] text-zinc-500">
                Brak auto-pollingu co kilka sekund. Odświeżenie następuje po wejściu i po operacjach użytkownika.
              </p>
              <div
                className="space-y-2 pr-2"
                style={{
                  maxHeight: "690px",
                  overflowY: "scroll",
                  scrollbarGutter: "stable",
                  overscrollBehavior: "contain",
                }}
              >
                {filteredAudit.map((entry) => (
                  <article key={entry.id} className="min-h-[44px] rounded-lg border border-zinc-800 p-3">
                    <div className="flex flex-wrap items-center gap-3 text-xs">
                      <span
                        className={`rounded border px-2 py-1 uppercase ${outcomeClass(
                          auditOutcome(entry.status)
                        )}`}
                      >
                        {entry.action} / {entry.status}
                      </span>
                      <span className="text-zinc-500">{entry.actor}</span>
                      <span className="text-zinc-500">{new Date(entry.timestamp).toLocaleString()}</span>
                    </div>
                  </article>
                ))}
              </div>
              </div>
            </div>
          </section>
        </>
      ) : null}

      {tab === "config" ? (
        <section className="glass-panel space-y-4 rounded-2xl border border-emerald-500/20 p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1">
              <span className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                {t("config.editStrategy")}
                <HelpBadge tip={help("config.editStrategy")} />
              </span>
              <select
                value={selectedStrategyId}
                onChange={(event) => {
                  const selectedId = event.target.value;
                  const found = strategies.find((item) => item.id === selectedId);
                  if (!found) {
                    return;
                  }
                  const normalized = normalizeStrategy(found);
                  setSelectedStrategyId(selectedId);
                  setConfigForm(normalized);
                  setStrategyName(normalized.name);
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
            <div className="space-y-1">
              <span className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                {t("config.activeStrategy")}
                <HelpBadge tip={help("config.activeStrategy")} />
              </span>
              <p className="rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100">
                {strategies.find((item) => item.id === activeStrategyId)?.name ?? "-"}
              </p>
            </div>
            <label className="space-y-1">
              <span className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                {t("config.strategyName")}
                <HelpBadge tip={help("config.strategyName")} />
              </span>
              <input
                value={strategyName}
                onChange={(event) => setStrategyName(event.target.value)}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
              />
            </label>
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            <label className="space-y-1">
              <span className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                {t("config.discoveryMode")}
                <HelpBadge tip={help("config.discoveryMode")} />
              </span>
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
              <span className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                {t("config.cacheTtl")}
                <HelpBadge tip={help("config.cacheTtl")} />
              </span>
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
              <span className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                {t("config.limit")}
                <HelpBadge tip={help("config.limit")} />
              </span>
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
              <span className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                {t("config.minScore")}
                <HelpBadge tip={help("config.minScore")} />
              </span>
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
              <span className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                {t("config.rss")}
                <HelpBadge tip={help("config.rss")} />
              </span>
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
              <p className="text-xs text-zinc-500">{t("config.rssHelp")}</p>
            </label>
          </div>

          <label className="space-y-1">
            <span className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
              {t("config.topicKeywords")}
              <HelpBadge tip={help("config.topicKeywords")} />
            </span>
            <textarea
              value={configForm.topic_keywords.join("\n")}
              onChange={(event) =>
                setConfigForm((previous) => ({
                  ...previous,
                  topic_keywords: normalizeTopicKeywords(event.target.value),
                }))
              }
              rows={3}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
            />
            <p className="text-xs text-zinc-500">{t("config.topicKeywordsHelp")}</p>
          </label>

          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-2">
              <p className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                {t("config.channels")}
                <HelpBadge tip={help("config.channels")} />
              </p>
              <div className="flex flex-wrap gap-2">
                {CHANNELS.map((value) => (
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
              <p className="inline-flex items-center gap-1 text-xs uppercase text-zinc-400">
                {t("config.languages")}
                <HelpBadge tip={help("config.languages")} />
              </p>
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
          {selectedStrategyId !== activeStrategyId ? (
            <p className="text-xs text-amber-300">
              Save applies to active strategy only. Activate selected strategy first.
            </p>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void saveConfig()}
              disabled={configLoading || selectedStrategyId !== activeStrategyId}
              className="rounded-xl border border-emerald-500/40 px-4 py-2 text-sm text-emerald-100 disabled:opacity-50"
              title={
                selectedStrategyId !== activeStrategyId
                  ? "Save updates only active strategy"
                  : undefined
              }
            >
              <span className="inline-flex items-center gap-1">
                {t("config.save")}
                <HelpBadge tip={help("config.save")} />
              </span>
            </button>
            <button
              type="button"
              onClick={() => void saveStrategy()}
              disabled={configLoading || !configForm.id}
              className="rounded-xl border border-cyan-500/40 px-4 py-2 text-sm text-cyan-100 disabled:opacity-50"
            >
              <span className="inline-flex items-center gap-1">
                {t("config.updateStrategy")}
                <HelpBadge tip={help("config.updateStrategy")} />
              </span>
            </button>
            <button
              type="button"
              onClick={() => void createStrategy()}
              disabled={configLoading || !strategyName.trim()}
              className="rounded-xl border border-violet-500/40 px-4 py-2 text-sm text-violet-100 disabled:opacity-50"
            >
              <span className="inline-flex items-center gap-1">
                {t("config.newStrategy")}
                <HelpBadge tip={help("config.newStrategy")} />
              </span>
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
              <span className="inline-flex items-center gap-1">
                {t("config.duplicate")}
                <HelpBadge tip={help("config.duplicate")} />
              </span>
            </button>
            <button
              type="button"
              onClick={() => void activateStrategy(configForm.id)}
              disabled={configLoading || !configForm.id}
              className="rounded-xl border border-amber-500/40 px-4 py-2 text-sm text-amber-100 disabled:opacity-50"
            >
              <span className="inline-flex items-center gap-1">
                {t("config.activate")}
                <HelpBadge tip={help("config.activate")} />
              </span>
            </button>
            <button
              type="button"
              onClick={() => {
                if (
                  typeof window !== "undefined" &&
                  window.confirm(
                    "Czy na pewno usunąć strategię? Tej operacji nie można cofnąć."
                  )
                ) {
                  void deleteStrategy(configForm.id);
                }
              }}
              disabled={configLoading || !configForm.id}
              className="rounded-xl border border-rose-500/40 px-4 py-2 text-sm text-rose-100 disabled:opacity-50"
            >
              <span className="inline-flex items-center gap-1">
                {t("config.delete")}
                <HelpBadge tip={help("config.delete")} />
              </span>
            </button>
            <button
              type="button"
              onClick={() => void refreshCandidatesNow()}
              disabled={configLoading}
              className="rounded-xl border border-cyan-500/30 px-4 py-2 text-sm text-cyan-100 disabled:opacity-50"
            >
              <span className="inline-flex items-center gap-1">
                {t("config.refreshNow")}
                <HelpBadge tip={help("config.refreshNow")} />
              </span>
            </button>
            <button
              type="button"
              onClick={() => void loadConfig()}
              disabled={configLoading}
              className="rounded-xl border border-zinc-700 px-4 py-2 text-sm text-zinc-100 disabled:opacity-50"
            >
              <span className="inline-flex items-center gap-1">
                {t("config.restore")}
                <HelpBadge tip={help("config.restore")} />
              </span>
            </button>
          </div>
        </section>
      ) : null}

      {tab === "integrations" ? (
        <section className="grid gap-4 lg:grid-cols-2">
          <div className="glass-panel space-y-4 rounded-2xl border border-violet-500/20 p-4">
            <h3 className="inline-flex items-center gap-2 text-base font-medium text-zinc-100">
              {t("tabs.integrations")}
              <HelpBadge tip={help("tabs.integrations")} />
            </h3>
            {integrationLoading ? <p className="text-zinc-400">{t("integrations.loading")}</p> : null}
            {integrationError ? <p className="text-rose-300">{integrationError}</p> : null}
            {!integrationLoading && !integrations.length ? (
              <p className="text-zinc-400">{t("integrations.empty")}</p>
            ) : null}
            <div className="max-h-[920px] space-y-3 overflow-y-auto pr-1">
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
                    <HelpBadge
                      tip={help(
                        item.requires_key ? "integrations.keyRequired" : "integrations.public"
                      )}
                    />
                  </div>
                  <p className="mt-2 text-sm text-zinc-300">{item.details}</p>
                  {item.key_hint ? (
                    <p className="mt-1 text-xs text-zinc-500">
                      {t("integrations.key")}: {item.key_hint}
                      <span className="ml-1 align-middle">
                        <HelpBadge tip={help("integrations.key")} />
                      </span>
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
                    <span className="inline-flex items-center gap-1">
                      {t("integrations.test")}
                      <HelpBadge tip={help("integrations.test")} />
                    </span>
                  </button>
                  {integrationTests[item.id] ? (
                    <p className="mt-2 text-xs text-cyan-200">{integrationTests[item.id]}</p>
                  ) : null}
                </article>
              ))}
            </div>
          </div>

          <div className="glass-panel space-y-3 rounded-2xl border border-violet-500/20 p-4">
            <h3 className="inline-flex items-center gap-2 text-base font-medium text-zinc-100">
              {t("accounts.title")}
              <HelpBadge tip={help("accounts.title")} />
            </h3>
            {!channelDescriptors.length ? (
              <p className="text-sm text-zinc-400">{t("accounts.empty")}</p>
            ) : null}
            <div className="max-h-[920px] space-y-3 overflow-y-auto pr-1">
              {channelDescriptors.map((descriptor) => (
                <article key={descriptor.id} className="rounded-xl border border-zinc-800 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h4 className="text-sm font-medium uppercase text-zinc-200">{descriptor.id}</h4>
                  <span className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-300">
                    {t("accounts.count")}: {descriptor.accounts_count}
                  </span>
                </div>
                <div className="mt-3 grid gap-2 md:grid-cols-[1fr_1fr_auto]">
                  <input
                    value={accountDraftDisplayNameByChannel[descriptor.id] ?? ""}
                    onChange={(event) =>
                      setAccountDraftDisplayNameByChannel((previous) => ({
                        ...previous,
                        [descriptor.id]: event.target.value,
                      }))
                    }
                    placeholder={t("accounts.displayName")}
                    className="rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                  />
                  <input
                    value={accountDraftTargetByChannel[descriptor.id] ?? ""}
                    onChange={(event) =>
                      setAccountDraftTargetByChannel((previous) => ({
                        ...previous,
                        [descriptor.id]: event.target.value,
                      }))
                    }
                    placeholder={t("accounts.target")}
                    className="rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                  />
                  <button
                    type="button"
                    onClick={() => void createChannelAccount(descriptor.id)}
                    disabled={integrationLoading}
                    className="rounded-lg border border-emerald-500/40 px-3 py-2 text-xs text-emerald-100 disabled:opacity-50"
                  >
                    <span className="inline-flex items-center gap-1">
                      {t("accounts.add")}
                      <HelpBadge tip={help("accounts.add")} />
                    </span>
                  </button>
                </div>
                <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-zinc-500">
                  <span className="inline-flex items-center gap-1">
                    {t("accounts.displayName")}
                    <HelpBadge tip={help("accounts.displayName")} />
                  </span>
                  <span className="inline-flex items-center gap-1">
                    {t("accounts.target")}
                    <HelpBadge tip={help("accounts.target")} />
                  </span>
                </div>
                <div className="mt-3 space-y-2">
                  {(accountsByChannel[descriptor.id] ?? []).map((account) => (
                    <div key={account.account_id} className="rounded-lg border border-zinc-800/80 p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-sm text-zinc-100">
                          {account.display_name}
                          {account.is_default ? (
                            <span className="ml-2 rounded bg-cyan-900/40 px-2 py-0.5 text-[10px] uppercase text-cyan-100">
                              {t("accounts.default")}
                            </span>
                          ) : null}
                        </p>
                        <span className="rounded bg-zinc-800 px-2 py-0.5 text-[10px] uppercase text-zinc-300">
                          {account.secret_status}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-zinc-400">
                        {t("accounts.target")}: {account.target || "-"}
                      </p>
                      <p className="mt-1 text-xs text-zinc-500">
                        Health: {account.last_test_status ?? account.secret_status}
                        {account.last_tested_at ? ` @ ${new Date(account.last_tested_at).toLocaleString()}` : ""}
                      </p>
                      <p className="mt-1 text-xs text-zinc-500">
                        Publish: ok={account.successful_publishes} / fail={account.failed_publishes}
                        {account.last_publish_status
                          ? ` (last: ${account.last_publish_status}${
                              account.last_published_at
                                ? ` @ ${new Date(account.last_published_at).toLocaleString()}`
                                : ""
                            })`
                          : ""}
                      </p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => void activateChannelAccount(descriptor.id, account.account_id)}
                          disabled={integrationLoading}
                          className="rounded-lg border border-amber-500/40 px-2 py-1 text-[11px] text-amber-100 disabled:opacity-50"
                        >
                          <span className="inline-flex items-center gap-1">
                            {t("accounts.setDefault")}
                            <HelpBadge tip={help("accounts.setDefault")} />
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => void testChannelAccount(descriptor.id, account.account_id)}
                          disabled={integrationLoading}
                          className="rounded-lg border border-violet-500/40 px-2 py-1 text-[11px] text-violet-100 disabled:opacity-50"
                        >
                          <span className="inline-flex items-center gap-1">
                            {t("accounts.test")}
                            <HelpBadge tip={help("accounts.test")} />
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => void deleteChannelAccount(descriptor.id, account.account_id)}
                          disabled={integrationLoading}
                          className="rounded-lg border border-rose-500/40 px-2 py-1 text-[11px] text-rose-100 disabled:opacity-50"
                        >
                          <span className="inline-flex items-center gap-1">
                            {t("accounts.delete")}
                            <HelpBadge tip={help("accounts.delete")} />
                          </span>
                        </button>
                      </div>
                      {integrationTests[`${descriptor.id}:${account.account_id}`] ? (
                        <p className="mt-2 text-xs text-cyan-200">
                          {integrationTests[`${descriptor.id}:${account.account_id}`]}
                        </p>
                      ) : null}
                    </div>
                  ))}
                </div>
                </article>
              ))}
            </div>
          </div>
        </section>
      ) : null}

      {tab === "monitoring" ? (
        <section className="space-y-6">
          <div className="glass-panel rounded-2xl border border-cyan-500/20 p-4 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-white">{t("monitoring.title")}</h2>
              <button
                type="button"
                onClick={() => void runMonitoringScan()}
                disabled={monitoringLoading}
                className="rounded-xl border border-emerald-500/40 px-4 py-2 text-sm text-emerald-100 disabled:opacity-50"
              >
                {monitoringLoading ? t("monitoring.scanning") : t("monitoring.scan")}
              </button>
            </div>
            {monitoringError ? <p className="text-rose-300 text-sm">{monitoringError}</p> : null}
            {monitoringSummary ? (
              <div className="grid gap-3 md:grid-cols-3">
                <div className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-3">
                  <p className="text-xs uppercase text-zinc-400">{t("monitoring.keywords")}</p>
                  <p className="text-2xl font-semibold text-white">{monitoringSummary.active_keywords}/{monitoringSummary.total_keywords}</p>
                </div>
                <div className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-3">
                  <p className="text-xs uppercase text-zinc-400">{t("monitoring.coverage")}</p>
                  <p className="text-2xl font-semibold text-emerald-300">{(monitoringSummary.owned_source_coverage * 100).toFixed(0)}%</p>
                </div>
                <div className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-3">
                  <p className="text-xs uppercase text-zinc-400">{t("monitoring.risks")}</p>
                  <p className={`text-2xl font-semibold ${monitoringSummary.risk_count > 0 ? "text-rose-300" : "text-white"}`}>{monitoringSummary.risk_count}</p>
                </div>
              </div>
            ) : null}
          </div>
          <div className="glass-panel rounded-2xl border border-cyan-500/20 p-4 space-y-3">
            <h3 className="text-sm font-semibold text-zinc-300">{t("monitoring.results")}</h3>
            {monitoringResults.length === 0 ? (
              <p className="text-zinc-400 text-sm">{t("monitoring.noResults")}</p>
            ) : (
              <div className="space-y-2">
                {monitoringResults.slice(0, monitoringResultsVisible).map((result) => (
                  <div key={result.result_id} className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-3 text-sm">
                    <div className="flex flex-wrap gap-2 mb-1 text-xs">
                      <span className={`rounded px-2 py-0.5 ${result.maps_to_base_source ? "bg-emerald-900/40 text-emerald-200" : result.classification === "brand_mention_risk" ? "bg-rose-900/40 text-rose-200" : "bg-zinc-800 text-zinc-300"}`}>
                        {result.classification}
                      </span>
                      <span className="rounded bg-zinc-800 px-2 py-0.5 text-zinc-300">#{result.position}</span>
                    </div>
                    <p className="text-zinc-200 font-medium">{result.title}</p>
                    <a href={result.url} target="_blank" rel="noopener noreferrer" className="text-xs text-cyan-400 hover:underline break-all">{result.url}</a>
                    <p className="text-xs text-zinc-400 mt-1">{result.snippet}</p>
                    <button
                      type="button"
                      onClick={() => void createCampaignFromResult(result.result_id, result.title)}
                      disabled={campaignsLoading}
                      className="mt-2 rounded-lg border border-emerald-500/30 px-2 py-1 text-xs text-emerald-200 hover:border-emerald-400 disabled:opacity-50"
                    >
                      {t("monitoring.createCampaign")}
                    </button>
                  </div>
                ))}
                {monitoringResults.length > monitoringResultsVisible ? (
                  <button
                    type="button"
                    onClick={() => setMonitoringResultsVisible((n) => n + 20)}
                    className="w-full rounded-xl border border-zinc-700 py-2 text-sm text-zinc-400 hover:text-zinc-200"
                  >
                    {t("monitoring.loadMore")} ({monitoringResults.length - monitoringResultsVisible} {t("monitoring.remaining")})
                  </button>
                ) : null}
              </div>
            )}
          </div>
        </section>
      ) : null}

      {tab === "sources" ? (
        <section className="space-y-6">
          <div className="glass-panel rounded-2xl border border-cyan-500/20 p-4 space-y-4">
            <h2 className="text-lg font-semibold text-white">{t("sources.title")}</h2>
            {sourcesError ? <p className="text-rose-300 text-sm">{sourcesError}</p> : null}
            <div className="grid gap-3 md:grid-cols-4">
              <input
                value={newSourceName}
                onChange={(e) => setNewSourceName(e.target.value)}
                placeholder={t("sources.name")}
                className="rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
              />
              <input
                value={newSourceUrl}
                onChange={(e) => setNewSourceUrl(e.target.value)}
                placeholder={t("sources.url")}
                className="rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
              />
              <select
                value={newSourceChannel}
                onChange={(e) => setNewSourceChannel(e.target.value as PublishChannel)}
                className="rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
              >
                {CHANNELS.map((ch) => <option key={ch} value={ch}>{ch}</option>)}
              </select>
              <button
                type="button"
                onClick={() => void addBaseSource()}
                disabled={sourcesLoading}
                className="rounded-xl border border-emerald-500/40 px-4 py-2 text-sm text-emerald-100 disabled:opacity-50"
              >
                {t("sources.add")}
              </button>
            </div>
          </div>
          <div className="space-y-2">
            {baseSources.length === 0 ? (
              <p className="text-zinc-400 text-sm">{t("sources.empty")}</p>
            ) : (
              baseSources.map((src) => (
                <div key={src.source_id} className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-3 flex items-center justify-between text-sm">
                  <div>
                    <span className="text-zinc-200 font-medium">{src.name}</span>
                    <span className="ml-2 text-xs text-zinc-400">{src.channel}</span>
                    <a href={src.base_url} target="_blank" rel="noopener noreferrer" className="ml-2 text-xs text-cyan-400 hover:underline">{src.base_url}</a>
                    {!src.enabled ? <span className="ml-2 rounded bg-zinc-700 px-1 text-xs text-zinc-400">{t("sources.disabled")}</span> : null}
                  </div>
                  <button
                    type="button"
                    onClick={() => void deleteBaseSource(src.source_id)}
                    disabled={sourcesLoading}
                    className="rounded-lg border border-rose-500/40 px-2 py-1 text-xs text-rose-100 disabled:opacity-50"
                  >
                    {t("sources.delete")}
                  </button>
                </div>
              ))
            )}
          </div>
        </section>
      ) : null}

      {tab === "keywords" ? (
        <section className="space-y-6">
          <div className="glass-panel rounded-2xl border border-cyan-500/20 p-4 space-y-4">
            <h2 className="text-lg font-semibold text-white">{t("keywords.title")}</h2>
            {keywordsError ? <p className="text-rose-300 text-sm">{keywordsError}</p> : null}
            <div className="grid gap-3 md:grid-cols-3">
              <input
                value={newKeywordPhrase}
                onChange={(e) => setNewKeywordPhrase(e.target.value)}
                placeholder={t("keywords.phrase")}
                className="rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
              />
              <select
                value={newKeywordType}
                onChange={(e) => setNewKeywordType(e.target.value as KeywordType)}
                className="rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
              >
                {(["brand_core", "brand_product", "brand_person", "risk_term", "competitor_context"] as const).map((kwType) => (
                  <option key={kwType} value={kwType}>{kwType}</option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => void addKeyword()}
                disabled={keywordsLoading}
                className="rounded-xl border border-emerald-500/40 px-4 py-2 text-sm text-emerald-100 disabled:opacity-50"
              >
                {t("keywords.add")}
              </button>
            </div>
          </div>
          <div className="space-y-2">
            {keywords.length === 0 ? (
              <p className="text-zinc-400 text-sm">{t("keywords.empty")}</p>
            ) : (
              keywords.map((kw) => (
                <div key={kw.keyword_id} className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-3 flex items-center justify-between text-sm">
                  <div className="flex items-center gap-2">
                    <span className="text-zinc-200 font-medium">{kw.phrase}</span>
                    <span className="rounded bg-cyan-900/40 px-2 py-0.5 text-xs text-cyan-200">{kw.keyword_type}</span>
                    <span className="text-xs text-zinc-400">P{kw.priority}</span>
                    {!kw.active ? <span className="rounded bg-zinc-700 px-1 text-xs text-zinc-400">{t("keywords.inactive")}</span> : null}
                  </div>
                  <button
                    type="button"
                    onClick={() => void deleteKeyword(kw.keyword_id)}
                    disabled={keywordsLoading}
                    className="rounded-lg border border-rose-500/40 px-2 py-1 text-xs text-rose-100 disabled:opacity-50"
                  >
                    {t("keywords.delete")}
                  </button>
                </div>
              ))
            )}
          </div>
        </section>
      ) : null}

      {tab === "campaigns" ? (
        <section className="space-y-6">
          <div className="glass-panel rounded-2xl border border-cyan-500/20 p-4 space-y-4">
            <h2 className="text-lg font-semibold text-white">{t("campaigns.title")}</h2>
            {campaignsError ? <p className="text-rose-300 text-sm">{campaignsError}</p> : null}
            <div className="grid gap-3 md:grid-cols-2">
              <input
                value={newCampaignName}
                onChange={(e) => setNewCampaignName(e.target.value)}
                placeholder={t("campaigns.name")}
                className="rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
              />
              <button
                type="button"
                onClick={() => void createCampaign()}
                disabled={campaignsLoading}
                className="rounded-xl border border-emerald-500/40 px-4 py-2 text-sm text-emerald-100 disabled:opacity-50"
              >
                {t("campaigns.create")}
              </button>
            </div>
          </div>
          <div className="space-y-3">
            {campaigns.length === 0 ? (
              <p className="text-zinc-400 text-sm">{t("campaigns.empty")}</p>
            ) : (
              campaigns.map((camp) => (
                <div key={camp.campaign_id} className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-4 text-sm">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-zinc-200 font-semibold">{camp.name}</span>
                      <span className={`rounded px-2 py-0.5 text-xs ${camp.status === "running" ? "bg-cyan-900/40 text-cyan-200" : camp.status === "completed" ? "bg-emerald-900/40 text-emerald-200" : camp.status === "failed" ? "bg-rose-900/40 text-rose-200" : "bg-zinc-800 text-zinc-300"}`}>
                        {camp.status}
                      </span>
                    </div>
                    {camp.status !== "completed" && camp.status !== "failed" && camp.status !== "cancelled" ? (
                      <button
                        type="button"
                        onClick={() => void runCampaign(camp.campaign_id)}
                        disabled={campaignsLoading || camp.status === "running"}
                        className="rounded-lg border border-emerald-500/40 px-3 py-1 text-xs text-emerald-100 disabled:opacity-50"
                      >
                        {t("campaigns.run")}
                      </button>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap gap-1 text-xs text-zinc-400">
                    {camp.channels.map((ch) => (
                      <span key={ch} className="rounded bg-zinc-800 px-2 py-0.5">{ch}</span>
                    ))}
                  </div>
                  <p className="text-xs text-zinc-500 mt-1">{t("campaigns.created")}: {new Date(camp.created_at).toLocaleString()}</p>
                  {(camp.draft_ids.length > 0 || camp.queue_ids.length > 0) ? (
                    <p className="mt-1 text-xs text-zinc-400">
                      {t("campaigns.drafts")}: {camp.draft_ids.length} · {t("campaigns.queued")}: {camp.queue_ids.length}
                    </p>
                  ) : null}
                </div>
              ))
            )}
          </div>
        </section>
      ) : null}
    </div>
  );
}
