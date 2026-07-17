import { TradeRepublicApi, createMessage, type Portfolio } from "trapi";
import { existsSync, readFileSync, renameSync, writeFileSync, unlinkSync, mkdirSync } from "fs";
import { homedir } from "os";
import { join as joinPath } from "path";

type JsonObject = Record<string, unknown>;
type PositionLite = {
  isin: string;
  name: string;
  netSize: number;
  isCrypto: boolean;
  averageBuyIn: number | null;
  assetType: string;
};

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null;
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const normalized = value.replace(",", ".").trim();
    const parsed = Number(normalized);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function getString(obj: JsonObject, key: string): string | null {
  const value = obj[key];
  return typeof value === "string" ? value : null;
}

function findNumberByLikelyKeys(root: unknown, keys: readonly string[]): number | null {
  const queue: unknown[] = [root];
  const targetKeys = new Set(keys.map((key) => key.toLowerCase()));

  while (queue.length > 0) {
    const current = queue.shift();
    if (Array.isArray(current)) {
      for (const item of current) queue.push(item);
      continue;
    }
    if (!isObject(current)) continue;

    for (const [key, value] of Object.entries(current)) {
      const keyLower = key.toLowerCase();
      if (targetKeys.has(keyLower)) {
        const numeric = toNumber(value);
        if (numeric !== null) return numeric;
      }
      if (isObject(value) || Array.isArray(value)) queue.push(value);
    }
  }
  return null;
}

async function subscribeOnceAsync(
  api: TradeRepublicApi,
  message: ReturnType<typeof createMessage>,
): Promise<string | null> {
  return await new Promise((resolve) => {
    api.subscribeOnce(message, (data) => resolve(data));
  });
}

async function subscribeOnceWithTimeout(
  api: TradeRepublicApi,
  message: ReturnType<typeof createMessage>,
  timeoutMs = 7000,
): Promise<string | null> {
  const timeoutPromise = new Promise<null>((resolve) => setTimeout(() => resolve(null), timeoutMs));
  return await Promise.race([subscribeOnceAsync(api, message), timeoutPromise]);
}

function extractTransactionsFromPayload(payload: unknown): unknown[] {
  if (Array.isArray(payload)) return payload;
  if (!isObject(payload)) return [];

  const keys = ["transactions", "items", "timelineTransactions", "data"];
  for (const key of keys) {
    const value = payload[key];
    if (Array.isArray(value)) return value;
  }
  return [];
}

function extractNextCursor(payload: unknown): string | null {
  if (!isObject(payload)) return null;

  const directKeys = [
    "nextCursor",
    "next_cursor",
    "cursorAfter",
    "cursor",
    "next",
    "after",
    "pageAfter",
    "nextCursorValue",
  ];

  for (const key of directKeys) {
    const value = (payload as Record<string, unknown>)[key];
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
    if (isObject(value) || Array.isArray(value)) {
      const nested = findStringByLikelyKeys(value, ["cursor", "after", "nextcursor", "token", "value"]);
      if (nested) return nested;
    }
  }

  // Cas courant: payload.cursors.after
  const cursors = (payload as { cursors?: unknown }).cursors;
  if (isObject(cursors)) {
    const after = (cursors as Record<string, unknown>).after;
    if (typeof after === "string" && after.trim()) return after;
    if (typeof after === "number" && Number.isFinite(after)) return String(after);
    if (isObject(after) || Array.isArray(after)) {
      const nested = findStringByLikelyKeys(after, ["cursor", "after", "nextcursor", "token", "value"]);
      if (nested) return nested;
    }
  }

  // Fallback générique: cherche dans tout l'objet.
  // (Evite de s'arrêter à la première page si la clé n'est pas exactement `cursors.after`.)
  const keys = ["nextCursor", "next_cursor", "next", "cursor", "after", "cursorAfter", "pageAfter", "paginationCursor"];
  const found = findStringByLikelyKeys(payload, keys);
  if (found && found.trim()) return found;

  return null;
}

async function fetchAllTransactions(
  api: TradeRepublicApi,
  maxTransactions: number,
  maxPages: number,
): Promise<{
  pages: number;
  total_items: number;
  transactions: unknown[];
  debug_tx_pagination?: {
    tx_limit: number | "infinity";
    max_pages: number | "infinity";
    break_reason: string;
    next_cursor_samples: Array<{
      page: number;
      next_cursor: string | null;
      page_items_count: number;
    }>;
  };
}> {
  const transactions: unknown[] = [];
  const seenCursors = new Set<string>();
  let cursor: string | null = null;
  let pages = 0;
  let breakReason = "unknown";
  const nextCursorSamples: Array<{
    page: number;
    next_cursor: string | null;
    page_items_count: number;
  }> = [];

  while (pages < maxPages) {
    const message = createMessage("timelineTransactions") as Record<string, unknown>;
    if (cursor) message.after = cursor;

    const raw = await subscribeOnceWithTimeout(api, message as ReturnType<typeof createMessage>, 10000);
    const parsed = tryParseJson(raw);
    if (!parsed) {
      breakReason = "parsed_null";
      break;
    }

    const pageItems = extractTransactionsFromPayload(parsed);
    transactions.push(...pageItems);
    pages += 1;
    const next = extractNextCursor(parsed);
    if (nextCursorSamples.length < 6) {
      nextCursorSamples.push({ page: pages, next_cursor: next, page_items_count: pageItems.length });
    }

    if (Number.isFinite(maxTransactions) && transactions.length >= maxTransactions) {
      breakReason = "tx_limit_reached";
      transactions.splice(maxTransactions);
      break;
    }
    if (!next) {
      breakReason = "next_cursor_null";
      break;
    }
    if (seenCursors.has(next)) {
      breakReason = "cursor_repeated";
      break;
    }

    seenCursors.add(next);
    cursor = next;
  }

  return {
    pages,
    total_items: transactions.length,
    transactions,
    debug_tx_pagination: {
      tx_limit: Number.isFinite(maxTransactions) ? maxTransactions : "infinity",
      max_pages: Number.isFinite(maxPages) ? maxPages : "infinity",
      break_reason: breakReason,
      next_cursor_samples: nextCursorSamples,
    },
  };
}

function compactTransaction(item: unknown): unknown {
  if (!isObject(item)) return item;

  const amountRaw = isObject(item.amount) ? item.amount : null;
  const compactAmount = amountRaw
    ? {
        currency: amountRaw.currency,
        value: amountRaw.value,
      }
    : null;

  return {
    id: item.id ?? null,
    timestamp: item.timestamp ?? null,
    title: item.title ?? null,
    amount: compactAmount,
  };
}

function tryParseJson(raw: string | null): unknown | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    const repaired = raw.trim().replace(/\]+$/g, "");
    try {
      return JSON.parse(repaired);
    } catch {
      return null;
    }
  }
}

function prettyJson(raw: string | null): string {
  if (!raw) return "null";
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    const repaired = raw.trim().replace(/\]+$/g, "");
    try {
      return JSON.stringify(JSON.parse(repaired), null, 2);
    } catch {
      return raw;
    }
  }
}

async function logDiagnosticPayload(
  api: TradeRepublicApi,
  label: string,
  message: ReturnType<typeof createMessage>,
): Promise<string | null> {
  const data = await subscribeOnceAsync(api, message);
  console.log(`\n=== DIAGNOSTIC ${label} ===`);
  console.log(prettyJson(data));
  return data;
}

function extractAccountNumbers(accountPairsRaw: unknown): string[] {
  const results = new Set<string>();
  const queue: unknown[] = [accountPairsRaw];
  const candidateKeys = ["secAccNo", "securitiesAccountNo", "securitiesAccountNumber", "accountId", "id"];

  while (queue.length > 0) {
    const current = queue.shift();
    if (Array.isArray(current)) {
      for (const item of current) queue.push(item);
      continue;
    }
    if (!isObject(current)) continue;

    for (const key of candidateKeys) {
      const value = getString(current, key);
      if (value && value.trim().length > 0) results.add(value);
    }

    for (const value of Object.values(current)) {
      if (isObject(value) || Array.isArray(value)) queue.push(value);
    }
  }
  return Array.from(results);
}

function extractPortfolioCompanies(portfolio: Portfolio): string[] {
  const categories = portfolio.categories.filter((category) => category.categoryType !== "cryptos");
  return Array.from(
    new Set(
      categories.flatMap((category) =>
        category.positions.map((position) =>
          position.derivativeInfo ? position.derivativeInfo.underlying.shortName : position.name,
        ),
      ),
    ),
  );
}

function extractPortfolioCryptos(portfolio: Portfolio): string[] {
  const categories = portfolio.categories.filter((category) => category.categoryType === "cryptos");
  return Array.from(
    new Set(categories.flatMap((category) => category.positions.map((position) => position.name))),
  );
}

function extractPortfolioPositions(portfolio: Portfolio): PositionLite[] {
  const out: PositionLite[] = [];
  for (const category of portfolio.categories) {
    for (const position of category.positions) {
      const netSize = toNumber(position.netSize);
      if (!position.isin || netSize === null) continue;
      out.push({
        isin: position.isin,
        name: position.name,
        netSize,
        isCrypto: category.categoryType === "cryptos" || position.instrumentType.toLowerCase() === "crypto",
        averageBuyIn: toNumber(position.averageBuyIn),
        assetType: position.instrumentType.toLowerCase(),
      });
    }
  }
  return out;
}

function extractPortfolioValuation(portfolioRaw: unknown): number | null {
  const accountLevelCandidates = [
    "totalvalue",
    "portfoliovalue",
    "total",
    "netvalue",
    "currentvalue",
    "marketvalue",
    "equity",
  ] as const;
  return findNumberByLikelyKeys(portfolioRaw, accountLevelCandidates);
}

function findStringByLikelyKeys(root: unknown, keys: readonly string[]): string | null {
  const queue: unknown[] = [root];
  const target = new Set(keys.map((k) => k.toLowerCase()));
  while (queue.length > 0) {
    const current = queue.shift();
    if (Array.isArray(current)) {
      for (const item of current) queue.push(item);
      continue;
    }
    if (!isObject(current)) continue;

    for (const [key, value] of Object.entries(current)) {
      if (target.has(key.toLowerCase()) && typeof value === "string" && value.trim()) return value;
      if (isObject(value) || Array.isArray(value)) queue.push(value);
    }
  }
  return null;
}

function extractExchangeId(homeExchangeRaw: unknown): string | null {
  const candidate = findStringByLikelyKeys(homeExchangeRaw, ["exchangeId", "homeExchangeId", "id", "exchange"]);
  if (!candidate) return null;
  const normalized = candidate.includes(".") ? candidate.split(".").pop() ?? candidate : candidate;
  return /^[A-Z]{3,5}$/.test(normalized) ? normalized : null;
}

function extractTickerPrice(tickerRaw: unknown): number | null {
  return (
    findNumberByLikelyKeys(tickerRaw, ["last", "price"]) ??
    findNumberByLikelyKeys(tickerRaw, ["bid", "price"]) ??
    findNumberByLikelyKeys(tickerRaw, ["ask", "price"])
  );
}

function computeCostBasisFromPortfolio(portfolio: Portfolio): number {
  let total = 0;
  for (const category of portfolio.categories) {
    for (const position of category.positions) {
      const avg = toNumber(position.averageBuyIn);
      const size = toNumber(position.netSize);
      if (avg !== null && size !== null) total += avg * size;
    }
  }
  return total;
}

/**
 * Détecte une erreur WebSocket "orpheline" : quand la session sauvegardée est
 * expirée, Trade Republic refuse le handshake (« Expected 101 status code »).
 * La lib `trapi` attrape déjà cette erreur (once("error")) et bascule sur le
 * login complet, mais le socket mort ré-émet un second event `error` de façon
 * asynchrone. Sans écouteur, Bun transforme cet ErrorEvent en Unhandled error
 * et tue le process (exit 1) AVANT que le flow needs_manual_auth ne s'exécute.
 * On neutralise donc uniquement ce bruit WS ; tout le reste crashe normalement.
 */
function isOrphanWebSocketError(err: unknown, depth = 0): boolean {
  if (depth > 5 || err == null) return false;

  const message =
    err instanceof Error
      ? err.message
      : isObject(err) && typeof (err as { message?: unknown }).message === "string"
        ? ((err as { message: string }).message)
        : String(err ?? "");
  const type = isObject(err) ? (err as { type?: unknown }).type : undefined;
  const code = isObject(err) ? (err as { code?: unknown }).code : undefined;

  if (
    type === "error" || // ws ErrorEvent (isTrusted/type)
    code === "ERR_UNHANDLED_ERROR" || // EventEmitter 'error' sans listener (emitError)
    /Expected 101 status code/i.test(message) ||
    /WebSocket connection to .* failed/i.test(message) ||
    /Invalid WebSocket frame/i.test(message)
  ) {
    return true;
  }

  // Node emballe un 'error' event orphelin en `ERR_UNHANDLED_ERROR` dont le
  // vrai ErrorEvent (« Expected 101… ») est planqué dans `.context` (parfois
  // `.cause` / `.error`). On déroule ces niveaux imbriqués.
  if (isObject(err)) {
    for (const key of ["context", "cause", "error"] as const) {
      const nested = (err as Record<string, unknown>)[key];
      if (nested != null && nested !== err && isOrphanWebSocketError(nested, depth + 1)) {
        return true;
      }
    }
  }
  return false;
}

async function main() {
  // Garde-fou global : empêche qu'un ErrorEvent WS orphelin ne fasse crasher
  // tout le bridge. Enregistré en premier pour couvrir toute la durée de vie.
  process.on("uncaughtException", (err) => {
    if (isOrphanWebSocketError(err)) {
      console.error(
        "[tr-bridge] ErrorEvent WebSocket orphelin ignoré:",
        err instanceof Error ? err.message : err,
      );
      return;
    }
    console.error("[tr-bridge] Exception non capturée:", err);
    process.exit(1);
  });
  process.on("unhandledRejection", (reason) => {
    if (isOrphanWebSocketError(reason)) {
      console.error(
        "[tr-bridge] Rejet WebSocket orphelin ignoré:",
        reason instanceof Error ? reason.message : reason,
      );
      return;
    }
    console.error("[tr-bridge] Rejet non géré:", reason);
    process.exit(1);
  });

  const [, , phoneNumber, pin, ...flags] = process.argv;
  const diagnosticsEnabled = flags.includes("--diag");
  const jsonMode = flags.includes("--json");
  const outFileFlagIndex = flags.indexOf("--out-file");
  const outFile =
    outFileFlagIndex >= 0 && outFileFlagIndex < flags.length - 1
      ? (flags[outFileFlagIndex + 1]?.trim() || null)
      : null;
  const devicePinFlagIndex = flags.indexOf("--device-pin");
  const devicePin =
    devicePinFlagIndex >= 0 && devicePinFlagIndex < flags.length - 1
      ? flags[devicePinFlagIndex + 1]?.trim() || null
      : null;
  const txLimitFlagIndex = flags.indexOf("--tx-limit");
  const txLimit =
    txLimitFlagIndex >= 0 && txLimitFlagIndex < flags.length - 1
      ? Number(flags[txLimitFlagIndex + 1])
      : 5000;
  // maxTransactions=Infinity => aucune limite (s'arrêtera uniquement via next-cursor/absence de next, etc.)
  const maxTransactions =
    Number.isFinite(txLimit) && txLimit > 0 ? Math.floor(txLimit) : Number.POSITIVE_INFINITY;

  const maxPagesFlagIndex = flags.indexOf("--max-pages");
  const maxPagesRaw =
    maxPagesFlagIndex >= 0 && maxPagesFlagIndex < flags.length - 1
      ? Number(flags[maxPagesFlagIndex + 1])
      : 5000;
  const maxPages =
    Number.isFinite(maxPagesRaw) && maxPagesRaw > 0 ? Math.floor(maxPagesRaw) : Number.POSITIVE_INFINITY;
  const verbose = !jsonMode;

  const emitStatusMarker = (status: string) => {
    // On utilise stdout uniquement pour un petit marqueur texte (pas du JSON),
    // afin que le backend sache s'il faut demander une 2FA.
    process.stdout.write(`TR_BRIDGE_STATUS:${status}\n`);
  };

  const emitJson = (payload: object) => {
    if (!outFile) {
      process.stdout.write(`${JSON.stringify(payload)}\n`);
      return;
    }

    // Écriture atomique: éviter que le backend lise un fichier partiellement écrit.
    // Le payload contient des données financières → 0600 dès la création du .tmp
    // (sinon la fenêtre d'exposition dure jusqu'au rename).
    const tmpPath = `${outFile}.tmp_${process.pid}`;
    writeFileSync(tmpPath, JSON.stringify(payload), { encoding: "utf-8", mode: 0o600 });
    renameSync(tmpPath, outFile);
  };

  // En mode JSON, on supprime tous les logs parasites (y compris ceux de dépendances
  // qui utilisent console.log) pour ne sortir qu'un seul objet JSON final.
  if (jsonMode) {
    // eslint-disable-next-line no-console
    console.log = () => {};
  }
  const log = (...args: unknown[]) => {
    if (verbose) console.log(...args);
  };

  if (!phoneNumber || !pin) {
    console.error("Usage: bun run index.ts <phoneNumber> <pin> [--diag]");
    process.exit(1);
  }

  // Fichier d'état inter-processus: permet de réutiliser processId + wafToken entre
  // le premier appel (qui déclenche l'envoi du SMS) et le second (qui soumet le code).
  // Il contient des données sensibles (cookies/wafToken) → dossier privé (0700) dans
  // le home de l'utilisateur, jamais dans le /tmp partagé (évite symlink/disclosure).
  const stateDir = joinPath(homedir(), ".cache", "tr-bridge");
  try {
    mkdirSync(stateDir, { recursive: true, mode: 0o700 });
  } catch { /* ignore */ }
  const stateFile = joinPath(
    stateDir,
    `state_${Buffer.from(phoneNumber).toString("hex").slice(0, 20)}.json`,
  );
  const STATE_TTL_MS = 10 * 60 * 1000; // 10 min = durée de validité du code TR

  const api = new TradeRepublicApi(phoneNumber, pin);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const apiAny = api as any;

  // Evite les boucles de reconnexion WebSocket (notamment sur code=3003).
  // Quand la session WS se ferme, on laisse le flow "full login / device pin"
  // gérer la demande de 2FA (un seul round), plutôt que de reconnecter X fois.
  if (typeof apiAny._scheduleReconnect === "function") {
    apiAny._scheduleReconnect = () => {
      /* no-op: reconnexion desactivee */
    };
  }

  // Fermeture propre du browser Chromium pour libérer la RAM
  const closeBrowser = async () => {
    try {
      if (apiAny.browser) {
        await apiAny.browser.close();
        apiAny.browser = undefined;
      }
    } catch { /* ignore */ }
  };

  // Remplace process.exit pour toujours fermer Chromium avant
  const safeExit = async (code: number, jsonPayload?: object) => {
    await closeBrowser();
    if (jsonPayload) {
      if (outFile) {
        const status = (jsonPayload as { status?: unknown }).status;
        if (typeof status === "string") emitStatusMarker(status);
      }
      emitJson(jsonPayload);
    }
    process.exit(code);
  };

  let loggedIn = false;
  let needsManualAuth = false;

  // ── Appel 2 : code 2FA fourni — réutilise l'état sauvegardé sans Chromium ──
  if (devicePin && existsSync(stateFile)) {
    try {
      const raw = readFileSync(stateFile, "utf-8");
      const state: { processId?: string; rawCookies?: string; wafToken?: string; savedAt?: number } = JSON.parse(raw);
      const age = Date.now() - (state.savedAt ?? 0);

      if (state.processId && age < STATE_TTL_MS) {
        log("Reprise de session avec code 2FA (processId sauvegardé, pas de Chromium).");
        apiAny.processId = state.processId;
        if (state.rawCookies) apiAny.rawCookies = state.rawCookies;
        // Injecter le WAF token sauvegardé → _verifyDevicePin n'a pas besoin de spawner Chromium
        if (state.wafToken) apiAny.currentWafToken = state.wafToken;

        await apiAny._verifyDevicePin(devicePin);
        await apiAny._setupWebSocket();
        // Persister la session pour que les prochains appels sautent aussi le login complet
        await apiAny._saveSessionToFile?.();
        try { unlinkSync(stateFile); } catch { /* ignore */ }
        loggedIn = true;
      }
    } catch (err) {
      log("Reprise de session échouée, tentative de login complet :", err);
      loggedIn = false;
    }
  }

  // ── Appel 1 ou fallback : login complet ────────────────────────────────────
  if (!loggedIn) {
    try {
      loggedIn = await api.login(async () => {
        if (devicePin) return devicePin;

        // Sauvegarder processId + cookies + wafToken pour l'appel 2 (évite de relancer Chromium)
        const processId: string | undefined = apiAny.processId;
        const rawCookies: string | undefined = apiAny.rawCookies;
        const wafToken: string | undefined = apiAny.currentWafToken;
        if (processId) {
          try {
            // Recréation propre en 0600 (unlink d'abord: writeFileSync ne rechmod pas
            // un fichier existant, et ça évite de suivre un éventuel lien pré-planté).
            try { unlinkSync(stateFile); } catch { /* ignore */ }
            writeFileSync(
              stateFile,
              JSON.stringify({ processId, rawCookies, wafToken, savedAt: Date.now() }),
              { mode: 0o600 },
            );
          } catch { /* ignore */ }
        }

        needsManualAuth = true;
        // Sortie propre : on ferme Chromium avant d'exit pour libérer la RAM
        await safeExit(2, jsonMode ? {
          status: "needs_manual_auth",
          reason: "device_pin_required",
          timestamp: new Date().toISOString(),
        } : undefined);
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error ?? "");
      if (message.includes("DEVICE_PIN_REQUIRED") || needsManualAuth) {
        await safeExit(2, jsonMode ? {
          status: "needs_manual_auth",
          reason: "device_pin_required",
          timestamp: new Date().toISOString(),
        } : undefined);
      }
      // 429 sur le login initial: on l'expose proprement
      if (message.includes("429") || message.includes("TOO_MANY_REQUESTS")) {
        const retryMatch = message.match(/"nextAttemptInSeconds"\s*:\s*(\d+)/);
        const retryAfter = retryMatch ? parseInt(retryMatch[1], 10) : null;
        await safeExit(3, jsonMode ? {
          status: "rate_limited",
          reason: "too_many_requests",
          retry_after_seconds: retryAfter,
          timestamp: new Date().toISOString(),
        } : undefined);
        if (!jsonMode) console.error(`Trop de tentatives. Réessaie dans ${retryAfter ?? "?"}s.`);
      }
      await closeBrowser();
      throw error;
    }
  }

  if (!loggedIn) {
    await safeExit(2, jsonMode ? {
      status: "needs_manual_auth",
      reason: "device_pin_required",
      timestamp: new Date().toISOString(),
    } : undefined);
    if (!jsonMode) {
      console.error("Connexion echouee.");
      await safeExit(1);
    }
  }

  const accountPairsData = diagnosticsEnabled
    ? await logDiagnosticPayload(api, "accountPairs", createMessage("accountPairs"))
    : await subscribeOnceAsync(api, createMessage("accountPairs"));
  let accountNumbers: string[] = [];

  if (accountPairsData) {
    const accountPairsRaw: unknown = JSON.parse(accountPairsData);
    accountNumbers = extractAccountNumbers(accountPairsRaw);
  }

  if (accountNumbers.length === 0) {
    log("Aucun compte extrait depuis accountPairs, utilisation du portfolio par defaut.");
    accountNumbers = ["default"];
  } else {
    log("Comptes detectes:", accountNumbers);
  }

  const transactionsResult = await fetchAllTransactions(api, maxTransactions, maxPages);
  log(`Transactions récupérées: ${transactionsResult.total_items} sur ${transactionsResult.pages} page(s)`);

  let totalValuation = 0;
  let knownValuations = 0;
  let totalMarketValue = 0;
  let totalMarketValueExCrypto = 0;
  let totalMarketValueCrypto = 0;
  let totalCostBasis = 0;
  let totalCostBasisExCrypto = 0;
  let totalCostBasisCrypto = 0;
  let totalCash = 0;
  const accountSummaries: Array<{
    account: string;
    societesValue: number;
    cryptoValue: number;
    totalValue: number;
    cashValue: number;
    totalWithCash: number;
    investedTotal: number;
    investedSocietes: number;
    investedCrypto: number;
  }> = [];

  for (const secAccNo of accountNumbers) {
    const portfolioStatusMessage = createMessage("portfolioStatus");
    const cashMessage = createMessage("cash");
    const availableCashMessage = createMessage("availableCash");

    let cashAmount: number | null = null;

    if (diagnosticsEnabled) {
      await logDiagnosticPayload(api, `portfolioStatus (${secAccNo})`, portfolioStatusMessage);
      const cashData = await logDiagnosticPayload(api, `cash (${secAccNo})`, cashMessage);
      if (cashData) {
        try {
          const parsedCash = JSON.parse(cashData) as JsonObject;
          cashAmount = toNumber(parsedCash.amount);
        } catch {
          //
        }
      }
      await logDiagnosticPayload(api, `availableCash (${secAccNo})`, availableCashMessage);
    } else {
      const cashData = await subscribeOnceAsync(api, cashMessage);
      if (cashData) {
        try {
          const parsedCash = JSON.parse(cashData) as JsonObject;
          cashAmount = toNumber(parsedCash.amount);
        } catch {
          //
        }
      }
    }

    const portfolioMessage =
      secAccNo === "default"
        ? createMessage("compactPortfolioByType")
        : createMessage("compactPortfolioByType", { secAccNo });

    const portfolioData = diagnosticsEnabled
      ? await logDiagnosticPayload(api, `compactPortfolioByType (${secAccNo})`, portfolioMessage)
      : await subscribeOnceAsync(api, portfolioMessage);

    if (!portfolioData) {
      log(`Compte ${secAccNo}: aucune donnee portfolio.`);
      continue;
    }

    const portfolioRaw: unknown = JSON.parse(portfolioData);
    const portfolio = portfolioRaw as Portfolio;
    const companies = extractPortfolioCompanies(portfolio);
    const cryptos = extractPortfolioCryptos(portfolio);
    const positions = extractPortfolioPositions(portfolio);
    const costBasis = computeCostBasisFromPortfolio(portfolio);
    const costBasisExCrypto = positions
      .filter((p) => !p.isCrypto && p.averageBuyIn !== null)
      .reduce((sum, p) => sum + (p.averageBuyIn as number) * p.netSize, 0);
    const costBasisCrypto = positions
      .filter((p) => p.isCrypto && p.averageBuyIn !== null)
      .reduce((sum, p) => sum + (p.averageBuyIn as number) * p.netSize, 0);
    const valuation = extractPortfolioValuation(portfolioRaw);

    let accountMarketValue = 0;
    let accountMarketValueExCrypto = 0;
    let accountMarketValueCrypto = 0;
    let pricedPositions = 0;
    let pricedPositionsExCrypto = 0;
    let pricedPositionsCrypto = 0;
    const exCryptoPositionsCount = positions.filter((p) => !p.isCrypto).length;
    const cryptoPositionsCount = positions.filter((p) => p.isCrypto).length;

    log(`Compte ${secAccNo} - societes:`, companies);
    log(`Compte ${secAccNo} - cryptos:`, cryptos);

    totalCostBasis += costBasis;
    totalCostBasisExCrypto += costBasisExCrypto;
    totalCostBasisCrypto += costBasisCrypto;

    if (cashAmount !== null) {
      totalCash += cashAmount;
      log(`Compte ${secAccNo} - cash: ${cashAmount.toFixed(2)} EUR`);
    } else {
      log(`Compte ${secAccNo} - cash: non disponible`);
    }

    if (valuation === null) {
      log(`Compte ${secAccNo} - valorisation: non trouvee dans le payload.`);
    } else {
      knownValuations += 1;
      totalValuation += valuation;
      log(`Compte ${secAccNo} - valorisation: ${valuation}`);
    }

    for (const position of positions) {
      const homeExchangeData = await subscribeOnceWithTimeout(
        api,
        createMessage("homeInstrumentExchange", { id: position.isin }),
        6000,
      );
      const homeExchangeRaw = tryParseJson(homeExchangeData);
      const exchangeId = extractExchangeId(homeExchangeRaw);
      if (!exchangeId) continue;

      const tickerData = await subscribeOnceWithTimeout(
        api,
        createMessage("ticker", { id: `${position.isin}.${exchangeId}` }),
        6000,
      );
      const tickerRaw = tryParseJson(tickerData);
      const price = extractTickerPrice(tickerRaw);
      if (price === null) continue;

      pricedPositions += 1;
      accountMarketValue += price * position.netSize;
      if (position.isCrypto) {
        pricedPositionsCrypto += 1;
        accountMarketValueCrypto += price * position.netSize;
      } else {
        pricedPositionsExCrypto += 1;
        accountMarketValueExCrypto += price * position.netSize;
      }
    }

    totalMarketValue += accountMarketValue;
    totalMarketValueExCrypto += accountMarketValueExCrypto;
    totalMarketValueCrypto += accountMarketValueCrypto;
    const cashValue = cashAmount ?? 0;
    accountSummaries.push({
      account: secAccNo,
      societesValue: accountMarketValueExCrypto,
      cryptoValue: accountMarketValueCrypto,
      totalValue: accountMarketValue,
      cashValue,
      totalWithCash: accountMarketValue + cashValue,
      investedTotal: costBasis,
      investedSocietes: costBasisExCrypto,
      investedCrypto: costBasisCrypto,
    });

    log(
      `Compte ${secAccNo} - positions pricees: total ${pricedPositions}/${positions.length}, societes ${pricedPositionsExCrypto}/${exCryptoPositionsCount}, crypto ${pricedPositionsCrypto}/${cryptoPositionsCount}`,
    );
  }

  if (knownValuations > 0) {
    log(`Valorisation totale (comptes detectes): ${totalValuation}`);
  } else {
    log(
      "Aucune valorisation numerique detectee automatiquement. Je peux ajuster le mapping si tu me partages un extrait JSON de portfolio/accountPairs.",
    );
  }

  const result = {
    timestamp: new Date().toISOString(),
    mode: "valuation+transactions",
    pages: transactionsResult.pages,
    total_items: transactionsResult.total_items,
    transactions: transactionsResult.transactions.map(compactTransaction),
    accounts: accountSummaries.map((summary) => ({
      account: summary.account,
      ...(summary.societesValue > 0 || summary.investedSocietes > 0
        ? {
            societes: {
              invested: Number(summary.investedSocietes.toFixed(2)),
              valuation: Number(summary.societesValue.toFixed(2)),
            },
          }
        : {}),
      ...(summary.cryptoValue > 0 || summary.investedCrypto > 0
        ? {
            crypto: {
              invested: Number(summary.investedCrypto.toFixed(2)),
              valuation: Number(summary.cryptoValue.toFixed(2)),
            },
          }
        : {}),
      invested_total: Number(summary.investedTotal.toFixed(2)),
      positions_total: Number(summary.totalValue.toFixed(2)),
      cash: Number(summary.cashValue.toFixed(2)),
      total_with_cash: Number(summary.totalWithCash.toFixed(2)),
    })),
    global: {
      societes: Number(totalMarketValueExCrypto.toFixed(2)),
      crypto: Number(totalMarketValueCrypto.toFixed(2)),
      positions_total: Number(totalMarketValue.toFixed(2)),
      cash: Number(totalCash.toFixed(2)),
      invested_total: Number(totalCostBasis.toFixed(2)),
      invested_societes: Number(totalCostBasisExCrypto.toFixed(2)),
      invested_crypto: Number(totalCostBasisCrypto.toFixed(2)),
      total_with_cash: Number((totalMarketValue + totalCash).toFixed(2)),
    },
    ...(transactionsResult.debug_tx_pagination ? { debug_tx_pagination: transactionsResult.debug_tx_pagination } : {}),
  };

  if (verbose) {
    console.log(`\n=== RESULTAT FINAL ===`);
    for (const account of result.accounts) {
      console.log(`Compte ${account.account}`);
      if (account.societes) {
        console.log(`  - Societes:`);
        console.log(`    - Investi: ${account.societes.invested.toFixed(2)} EUR`);
        console.log(`    - Valo: ${account.societes.valuation.toFixed(2)} EUR`);
      }
      if (account.crypto) {
        console.log(`  - Crypto:`);
        console.log(`    - Investi: ${account.crypto.invested.toFixed(2)} EUR`);
        console.log(`    - Valo: ${account.crypto.valuation.toFixed(2)} EUR`);
      }
      console.log(`  - Investi total: ${account.invested_total.toFixed(2)} EUR`);
      console.log(`  - Valo totale positions: ${account.positions_total.toFixed(2)} EUR`);
      console.log(`  - Cash: ${account.cash.toFixed(2)} EUR`);
      console.log(`  - Total compte (positions + cash): ${account.total_with_cash.toFixed(2)} EUR`);
    }
    console.log(`Total global societes: ${result.global.societes.toFixed(2)} EUR`);
    console.log(`Total global crypto: ${result.global.crypto.toFixed(2)} EUR`);
    console.log(`Total global positions: ${result.global.positions_total.toFixed(2)} EUR`);
    console.log(`Total global cash: ${result.global.cash.toFixed(2)} EUR`);
    console.log(`Total global investi: ${result.global.invested_total.toFixed(2)} EUR`);
    console.log(`Total global investi societes: ${result.global.invested_societes.toFixed(2)} EUR`);
    console.log(`Total global investi crypto: ${result.global.invested_crypto.toFixed(2)} EUR`);
    console.log(`TOTAL GLOBAL (positions + cash): ${result.global.total_with_cash.toFixed(2)} EUR`);
  }

  if (jsonMode) {
    if (outFile) emitStatusMarker("authenticated");
    emitJson(result);
  }

  // Fermer proprement Chromium avant de sortir pour libérer la RAM immédiatement
  await closeBrowser();
  process.exit(0);
}

main().catch((error) => {
  console.error("Erreur pendant le test:", error);
  process.exit(1);
});
