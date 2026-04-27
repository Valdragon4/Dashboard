# Integration Trade Republic dans Django

Ce document explique comment integrer le mecanisme de valorisation (base sur `trapi` en Bun/TypeScript) dans un projet Django, pour recuperer automatiquement:

- les valorisations live (societes/crypto/total/cash),
- les montants investis (total, societes, crypto, par type d'actif),
- et gerer proprement l'authentification (session expiree, `3003`, auth manuelle).

## 1) Prerequis

- **Systeme**
  - Windows/Linux/macOS
  - Node/Bun installe (ici: Bun)
- **Compte Trade Republic**
  - Numero de telephone + PIN
  - Gestion du device PIN (2FA) lors de la premiere connexion ou expiration de session
- **Projet Python**
  - Django installe
  - (Optionnel recommande) Celery + Redis pour scheduler
- **Securite**
  - Variables d'environnement pour secrets (jamais de credentials en dur)

## 2) Architecture recommandee

Separer en deux briques:

1. **Service Bun/TypeScript** (client TR)
   - Se connecte a TR
   - Calcule la valorisation live par compte
   - Calcule les montants investis (cost basis) globaux et par type d'actif
   - Intercepte `WS close code 3003` sans boucle de retries
   - Expose un statut d'authentification exploitable par Django
   - Expose un endpoint HTTP JSON (ex: `GET /valuation`)
2. **Application Django**
   - Appelle le endpoint
   - Parse le JSON
   - Stocke snapshots et historique
   - Expose API/UI Python

Avantage: Django reste 100% Python, la logique API TR reste dans l'ecosysteme JS/TS du package.

## 3) Structure de fichiers conseillee

Exemple mono-repo:

```text
project-root/
  tr-bridge/
    package.json
    src/
      server.ts
      valuation.ts
  django-app/
    manage.py
    requirements.txt
    config/
    portfolio/
      models.py
      services/
        tr_bridge_client.py
      management/
        commands/
          sync_tr_valuation.py
```

## 4) Cote Bun/TS: fichiers type

## `tr-bridge/package.json`

```json
{
  "name": "tr-bridge",
  "type": "module",
  "scripts": {
    "dev": "bun run src/server.ts",
    "start": "bun run src/server.ts"
  },
  "dependencies": {
    "trapi": "NightOwl07/trade-republic-api"
  }
}
```

## `tr-bridge/src/valuation.ts` (exemple)

```ts
import { TradeRepublicApi, createMessage, type Portfolio } from "trapi";

type AccountValuation = {
  account: string;
  invested_total: number;
  invested_societes: number;
  invested_crypto: number;
  invested_by_asset_type: Record<string, number>;
  societes: number;
  crypto: number;
  positions_total: number;
  cash: number;
  total_with_cash: number;
};

type GlobalValuation = {
  invested_total: number;
  invested_societes: number;
  invested_crypto: number;
  invested_by_asset_type: Record<string, number>;
  societes: number;
  crypto: number;
  positions_total: number;
  cash: number;
  total_with_cash: number;
};

export type ValuationPayload = {
  timestamp: string;
  accounts: AccountValuation[];
  global: GlobalValuation;
};

async function subscribeOnce(api: TradeRepublicApi, msg: ReturnType<typeof createMessage>) {
  return await new Promise<string | null>((resolve) => {
    api.subscribeOnce(msg, (data) => resolve(data));
  });
}

function toNumber(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = Number(v.replace(",", "."));
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}

function parseAccountNumbers(raw: any): string[] {
  const accounts = Array.isArray(raw?.accounts) ? raw.accounts : [];
  return accounts
    .map((a: any) => a?.securitiesAccountNumber)
    .filter((x: unknown): x is string => typeof x === "string" && x.length > 0);
}

export async function computeValuation(phone: string, pin: string): Promise<ValuationPayload> {
  const api = new TradeRepublicApi(phone, pin);
  const ok = await api.login();
  if (!ok) throw new Error("Login Trade Republic impossible");

  const accountPairsRaw = await subscribeOnce(api, createMessage("accountPairs"));
  const accountPairs = accountPairsRaw ? JSON.parse(accountPairsRaw) : {};
  const accounts = parseAccountNumbers(accountPairs);

  const accountIds = accounts.length ? accounts : ["default"];
  const accountRows: AccountValuation[] = [];

  for (const accountId of accountIds) {
    const portfolioMsg =
      accountId === "default"
        ? createMessage("compactPortfolioByType")
        : createMessage("compactPortfolioByType", { secAccNo: accountId });

    const cashRaw = await subscribeOnce(api, createMessage("cash"));
    const cash = cashRaw ? toNumber(JSON.parse(cashRaw)?.amount) : 0;

    const portfolioRaw = await subscribeOnce(api, portfolioMsg);
    const portfolio = (portfolioRaw ? JSON.parse(portfolioRaw) : { categories: [] }) as Portfolio;

    // Ici tu peux reprendre ta logique de pricing live (ticker/homeInstrumentExchange)
    // Pour exemple: placeholders a remplacer par ton calcul reel.
    const investedTotal = 0;
    const investedSocietes = 0;
    const investedCrypto = 0;
    const investedByAssetType: Record<string, number> = {};
    const societes = 0;
    const crypto = 0;
    const positionsTotal = societes + crypto;

    accountRows.push({
      account: accountId,
      invested_total: investedTotal,
      invested_societes: investedSocietes,
      invested_crypto: investedCrypto,
      invested_by_asset_type: investedByAssetType,
      societes,
      crypto,
      positions_total: positionsTotal,
      cash,
      total_with_cash: positionsTotal + cash,
    });
  }

  const global = accountRows.reduce(
    (acc, row) => {
      acc.invested_total += row.invested_total;
      acc.invested_societes += row.invested_societes;
      acc.invested_crypto += row.invested_crypto;
      for (const [assetType, amount] of Object.entries(row.invested_by_asset_type)) {
        acc.invested_by_asset_type[assetType] =
          (acc.invested_by_asset_type[assetType] ?? 0) + amount;
      }
      acc.societes += row.societes;
      acc.crypto += row.crypto;
      acc.positions_total += row.positions_total;
      acc.cash += row.cash;
      acc.total_with_cash += row.total_with_cash;
      return acc;
    },
    {
      invested_total: 0,
      invested_societes: 0,
      invested_crypto: 0,
      invested_by_asset_type: {},
      societes: 0,
      crypto: 0,
      positions_total: 0,
      cash: 0,
      total_with_cash: 0,
    },
  );

  return {
    timestamp: new Date().toISOString(),
    accounts: accountRows,
    global,
  };
}
```

## `tr-bridge/src/server.ts` (endpoint HTTP)

```ts
import { computeValuation } from "./valuation";

const PORT = Number(process.env.TR_BRIDGE_PORT ?? 8787);
const PHONE = process.env.TR_PHONE;
const PIN = process.env.TR_PIN;
const TOKEN = process.env.TR_BRIDGE_TOKEN; // optionnel

if (!PHONE || !PIN) {
  throw new Error("TR_PHONE et TR_PIN sont requis");
}

Bun.serve({
  port: PORT,
  async fetch(req) {
    const url = new URL(req.url);

    if (url.pathname === "/health") {
      return new Response(JSON.stringify({ ok: true }), {
        headers: { "content-type": "application/json" },
      });
    }

    if (url.pathname === "/valuation") {
      if (TOKEN) {
        const auth = req.headers.get("authorization") ?? "";
        if (auth !== `Bearer ${TOKEN}`) {
          return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
        }
      }

      try {
        const payload = await computeValuation(PHONE, PIN);
        return new Response(JSON.stringify(payload), {
          headers: { "content-type": "application/json" },
        });
      } catch (err) {
        return new Response(
          JSON.stringify({ error: "valuation_failed", detail: String(err) }),
          { status: 500 },
        );
      }
    }

    if (url.pathname === "/auth/status") {
      // Exemple: brancher ici ton etat interne (authenticated / needs_manual_auth / failed)
      return new Response(
        JSON.stringify({
          status: "authenticated",
        }),
        { headers: { "content-type": "application/json" } },
      );
    }

    return new Response("Not found", { status: 404 });
  },
});

console.log(`TR bridge listening on :${PORT}`);
```

## 5) Variables d'environnement (exemple)

## `tr-bridge/.env` (exemple local)

```env
TR_PHONE=+33600000000
TR_PIN=1234
TR_BRIDGE_PORT=8787
TR_BRIDGE_TOKEN=super-secret-token
```

> En prod: stocker ces secrets dans un coffre (Vault, AWS Secrets Manager, etc.), pas en clair dans un fichier versionne.

## 6) Cote Django: integration

## `django-app/portfolio/services/tr_bridge_client.py`

```python
import requests
from django.conf import settings


class TradeRepublicBridgeError(Exception):
    pass


def fetch_tr_valuation():
    url = f"{settings.TR_BRIDGE_BASE_URL}/valuation"
    headers = {}
    if settings.TR_BRIDGE_TOKEN:
        headers["Authorization"] = f"Bearer {settings.TR_BRIDGE_TOKEN}"

    try:
        resp = requests.get(url, headers=headers, timeout=45)
    except requests.RequestException as exc:
        raise TradeRepublicBridgeError(f"bridge_unreachable: {exc}") from exc

    if resp.status_code != 200:
        raise TradeRepublicBridgeError(f"bridge_error status={resp.status_code} body={resp.text}")

    return resp.json()


def fetch_tr_auth_status():
    url = f"{settings.TR_BRIDGE_BASE_URL}/auth/status"
    headers = {}
    if settings.TR_BRIDGE_TOKEN:
        headers["Authorization"] = f"Bearer {settings.TR_BRIDGE_TOKEN}"
    resp = requests.get(url, headers=headers, timeout=20)
    if resp.status_code != 200:
        raise TradeRepublicBridgeError(f"auth_status_error status={resp.status_code} body={resp.text}")
    return resp.json()
```

## `django-app/portfolio/models.py` (exemple minimal)

```python
from django.db import models


class ValuationSnapshot(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    source_timestamp = models.DateTimeField()

    invested_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    invested_societes = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    invested_crypto = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    invested_by_asset_type = models.JSONField(default=dict)

    societes = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    crypto = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    positions_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cash = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_with_cash = models.DecimalField(max_digits=18, decimal_places=2, default=0)


class AccountValuationSnapshot(models.Model):
    snapshot = models.ForeignKey(
        ValuationSnapshot, on_delete=models.CASCADE, related_name="accounts"
    )
    account = models.CharField(max_length=64)

    invested_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    invested_societes = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    invested_crypto = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    invested_by_asset_type = models.JSONField(default=dict)

    societes = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    crypto = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    positions_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cash = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_with_cash = models.DecimalField(max_digits=18, decimal_places=2, default=0)
```

## `django-app/portfolio/management/commands/sync_tr_valuation.py`

```python
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from portfolio.models import ValuationSnapshot, AccountValuationSnapshot
from portfolio.services.tr_bridge_client import fetch_tr_valuation


class Command(BaseCommand):
    help = "Synchronise la valorisation Trade Republic depuis le bridge Bun"

    def handle(self, *args, **options):
        payload = fetch_tr_valuation()
        ts = parse_datetime(payload["timestamp"])
        global_data = payload["global"]

        snapshot = ValuationSnapshot.objects.create(
            source_timestamp=ts,
            invested_total=Decimal(str(global_data["invested_total"])),
            invested_societes=Decimal(str(global_data["invested_societes"])),
            invested_crypto=Decimal(str(global_data["invested_crypto"])),
            invested_by_asset_type=global_data["invested_by_asset_type"],
            societes=Decimal(str(global_data["societes"])),
            crypto=Decimal(str(global_data["crypto"])),
            positions_total=Decimal(str(global_data["positions_total"])),
            cash=Decimal(str(global_data["cash"])),
            total_with_cash=Decimal(str(global_data["total_with_cash"])),
        )

        for row in payload["accounts"]:
            AccountValuationSnapshot.objects.create(
                snapshot=snapshot,
                account=row["account"],
                invested_total=Decimal(str(row["invested_total"])),
                invested_societes=Decimal(str(row["invested_societes"])),
                invested_crypto=Decimal(str(row["invested_crypto"])),
                invested_by_asset_type=row["invested_by_asset_type"],
                societes=Decimal(str(row["societes"])),
                crypto=Decimal(str(row["crypto"])),
                positions_total=Decimal(str(row["positions_total"])),
                cash=Decimal(str(row["cash"])),
                total_with_cash=Decimal(str(row["total_with_cash"])),
            )

        self.stdout.write(self.style.SUCCESS(f"Snapshot cree: {snapshot.id}"))
```

## `django-app/settings.py` (extrait)

```python
TR_BRIDGE_BASE_URL = "http://127.0.0.1:8787"
TR_BRIDGE_TOKEN = "super-secret-token"
```

## 7) Procedure d'integration pas a pas

1. Creer le dossier bridge (`tr-bridge`) et installer deps Bun.
2. Implementer `valuation.ts` en reutilisant ta logique actuelle de pricing live.
3. Ajouter `server.ts` avec endpoint `/valuation`.
4. Lancer le bridge:
   - `bun run src/server.ts`
5. Cote Django:
   - ajouter service client HTTP
   - ajouter models snapshots
   - creer migrations
   - ajouter commande `sync_tr_valuation`
6. Tester:
   - `python manage.py sync_tr_valuation`
7. Automatiser:
   - cron/Celery Beat toutes les 5-15 minutes.
8. Ajouter le flux d'auth manuelle:
   - endpoint bridge `GET /auth/status`
   - page Django "Auth TR requise"
   - formulaire de saisie code device PIN (si necessaire)

## 8) Contrat JSON recommande

Exemple de reponse attendue depuis le bridge:

```json
{
  "timestamp": "2026-04-26T18:25:00.000Z",
  "accounts": [
    {
      "account": "0276377602",
      "invested_total": 11399.37,
      "invested_societes": 7000.12,
      "invested_crypto": 4399.25,
      "invested_by_asset_type": {
        "stock": 4200.12,
        "fund": 2800.0,
        "crypto": 4399.25
      },
      "societes": 7700.12,
      "crypto": 3600.45,
      "positions_total": 11300.57,
      "cash": 117.16,
      "total_with_cash": 11417.73
    }
  ],
  "global": {
    "invested_total": 15009.52,
    "invested_societes": 10610.27,
    "invested_crypto": 4399.25,
    "invested_by_asset_type": {
      "stock": 6500.12,
      "fund": 4110.15,
      "crypto": 4399.25
    },
    "societes": 12000.11,
    "crypto": 3800.77,
    "positions_total": 15800.88,
    "cash": 117.16,
    "total_with_cash": 15918.04
  }
}
```

## 9) Points d'attention production

- **Session/TR login**
  - gerer expiration token + re-auth
  - monitorer les erreurs websocket (3003, timeout, etc.)
  - sur `3003`: stopper la boucle de reconnect auto et basculer en refresh/auth flow
- **Robustesse**
  - timeout reseau
  - retry exponentiel
  - fallback partiel (positions pricees X/Y)
  - eviter les retries agressifs qui menent a `429`
- **Securite**
  - endpoint protege par token
  - idealement acces reseau prive uniquement
- **Observabilite**
  - logs JSON
  - metriques de latence et taux de succes

## 10) Commandes utiles

Bridge:

```bash
bun install
bun run src/server.ts
```

Test endpoint:

```bash
curl -H "Authorization: Bearer super-secret-token" http://127.0.0.1:8787/valuation
```

Django:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py sync_tr_valuation
```

## 11) Mode JSON pour integration Django/Python

Pour automatiser facilement, le script bridge doit exposer un mode `--json` qui renvoie un objet unique JSON en sortie.

### Pourquoi c'est important

En shell, toute sortie `stdout` est capturee. Si des logs texte sont melanges au JSON, le parse peut echouer.

### Recommandation

- En mode `--json`, n'imprimer que `JSON.stringify(result)` sur `stdout`.
- Rediriger les logs techniques vers `stderr` (ou les desactiver).

### Exemple PowerShell (robuste)

Si des logs parasites existent encore, recuperer uniquement la derniere ligne:

```powershell
$json = (& "C:\Users\bluef\.bun\bin\bun.exe" run index.ts "+33600000000" "1234" --json | Select-Object -Last 1)
$obj = $json | ConvertFrom-Json
```

Ensuite, exploiter les champs:

```powershell
$obj.global.total_with_cash
$obj.accounts[0].societes.valuation
```

### Exemple Python (Django service)

```python
import json
import subprocess

proc = subprocess.run(
    [
        r"C:\Users\bluef\.bun\bin\bun.exe",
        "run",
        "index.ts",
        "+33600000000",
        "1234",
        "--json",
    ],
    cwd=r"c:\Users\bluef\test",
    capture_output=True,
    text=True,
    check=True,
)

# Si logs melanges, garder la derniere ligne
json_line = proc.stdout.strip().splitlines()[-1]
payload = json.loads(json_line)
```

### Contract attendu (rappel)

- `accounts[]` avec categories optionnelles (`societes`, `crypto`) seulement si > 0
- `global` avec totaux consolides
- `timestamp` ISO8601

## 11) Comportement recommande sur `3003`

Objectif: eviter les boucles de reconnexion websocket qui saturent et peuvent provoquer du `429`.

- A la premiere fermeture WS `code=3003`:
  - marquer la session `stale`,
  - ne pas relancer `_scheduleReconnect()` en boucle,
  - lancer un refresh de session.
- Si refresh impossible:
  - exposer `status=needs_manual_auth` via `/auth/status`,
  - interrompre `/valuation` avec un statut explicite (ex: `409`).

Exemple de logique dans le client WS:

```ts
if (code === 3003) {
  // session invalide/expiree: pas de boucle de retries WS
  this.ws = undefined;
  this.authState = "needs_manual_auth";
  return;
}
this._scheduleReconnect();
```

## 12) Contrat JSON etat auth (recommande)

`GET /auth/status`

```json
{
  "status": "needs_manual_auth",
  "reason": "device_pin_required"
}
```

Valeurs possibles de `status`:

- `authenticated`
- `needs_manual_auth`
- `failed`

---

Si tu veux, prochaine etape: je peux te fournir une version "copier-coller" de `valuation.ts` adaptee exactement a ton `index.ts` actuel (avec la meme logique de separation `societes/crypto/total/cash`).
