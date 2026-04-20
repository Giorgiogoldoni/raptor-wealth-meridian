"""
fetch_rwm.py — RWM ETF Universe Builder
Repo: Giorgiogoldoni/raptor-wealth-meridian

Flusso:
  1. Legge levels.json da compass         → Layer 1 (85 ETF fissi)
  2. Legge market_data_rse.json da RAPTOR → regime macro
  3. Legge geografia/settoriali/tematici  → Layer 2 (ETF qualificati)
  4. Rileva entrati/usciti rispetto al giorno prima → changelog
  5. Scarica prezzi via yfinance
  6. Calcola RWM Score per ogni ETF
  7. Scrive etf_universe.json  → RWM/data/ + compass/data/
  8. Aggiorna etf_changelog.json → RWM/data/ (storico permanente)

Secrets: RWM_PAT (write su RWM + compass)
"""

import os
import json
import base64
import logging
import requests
import numpy as np
import yfinance as yf
from datetime import datetime, timezone

# ─── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("fetch_rwm")

# ─── CONFIG ─────────────────────────────────────────────────────────────────
GITHUB_TOKEN = os.environ["RWM_PAT"]

REPO_RWM     = "Giorgiogoldoni/raptor-wealth-meridian"
REPO_COMPASS = "Giorgiogoldoni/compass"
REPO_RAPTOR  = "Giorgiogoldoni/RAPTOR_SCENARIO_ENGINE"
REPO_GEO     = "Giorgiogoldoni/raptor-geografia"
REPO_SETT    = "Giorgiogoldoni/raptor-settoriali"
REPO_TEMI    = "Giorgiogoldoni/raptor-tematici"

PATH_UNIVERSE_RWM     = "data/etf_universe.json"
PATH_CHANGELOG_RWM    = "data/etf_changelog.json"
PATH_LEVELS           = "data/levels.json"
PATH_RSE              = "data/market_data_rse.json"
PATH_GEO              = "geografia.json"
PATH_SETT             = "settoriali.json"
PATH_TEMI             = "tematici.json"
PATH_UNIVERSE_COMPASS = "data/etf_universe.json"

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

# ─── CATEGORIA ETF ───────────────────────────────────────────────────────────
ETF_CATEGORY = {
    "XEON.MI":"monetario","SMART.MI":"monetario","IU0E.MI":"monetario",
    "XEOD.MI":"monetario","ERNX.MI":"monetario",
    "IBTE.MI":"obbligaz","SXRM.MI":"obbligaz","GOM.MI":"obbligaz",
    "IBTM.MI":"obbligaz","EUNH.MI":"obbligaz","SXRV.MI":"obbligaz",
    "AGGH.MI":"obbligaz","IGLO.MI":"obbligaz","IEAC.MI":"obbligaz",
    "IEAG.MI":"obbligaz","VAGE.MI":"obbligaz","EMBE.MI":"obbligaz",
    "EUHA.MI":"obbligaz","XUTC.MI":"obbligaz","XUCS.MI":"obbligaz",
    "JNHD.MI":"obbligaz","2B70.MI":"obbligaz","SEMB.MI":"obbligaz",
    "IHYU.MI":"hy","EUHI.MI":"hy","HYLD.MI":"hy","DHYA.MI":"hy","STHE.MI":"hy",
    "IPRP.MI":"real_estate","IWDP.MI":"real_estate",
    "XREA.MI":"real_estate","REET.MI":"real_estate",
    "PHAU.MI":"commodity","SILVER.MI":"commodity","COPA.MI":"commodity",
    "CMOD.MI":"commodity","AIGA.MI":"commodity","RARE.MI":"commodity",
    "VHYL.MI":"azionario","IDVY.MI":"azionario","FGEQ.MI":"azionario",
    "EUDV.MI":"azionario","TDIV.MI":"azionario","WENT.MI":"azionario",
    "EMDV.MI":"azionario","DHS.MI":"azionario","IUSA.MI":"azionario",
    "VUSA.MI":"azionario","MEUD.MI":"azionario","SWDA.MI":"azionario",
    "VWCE.DE":"azionario","EXX5.DE":"azionario","EXV1.DE":"azionario",
    "EXXW.DE":"azionario","IS3N.MI":"azionario","VAPX.MI":"azionario",
    "JPNH.MI":"azionario","CSPX.MI":"azionario","XDWT.MI":"azionario",
    "ISPA.DE":"azionario","ESGE.MI":"azionario",
    "DFNS.MI":"azionario","SMH.MI":"azionario","IFFF.MI":"azionario",
    "XAIX.MI":"azionario","WHCS.MI":"azionario","QNTM.MI":"azionario",
    "EQQQ.MI":"azionario","IUIT.MI":"azionario","NTSX.MI":"azionario",
    "NTSG.MI":"azionario","NTSZ.MI":"azionario","WRTY.MI":"azionario",
    "WSPE.MI":"azionario","WSPX.MI":"azionario","WWRD.MI":"azionario",
    "WS5X.MI":"azionario",
    "L2SP.MI":"leva","UC44.MI":"leva","2LVE.MI":"leva","2NVD.MI":"leva",
    "3USL.MI":"leva","QQQ3.MI":"leva","3EUL.MI":"leva","3NVD.MI":"leva",
}

SHOCK_2008 = {
    "monetario":2.0,"obbligaz":8.0,"hy":30.0,
    "real_estate":40.0,"commodity":20.0,"azionario":52.0,"leva":80.0,
}

# ─── GITHUB API ──────────────────────────────────────────────────────────────
def github_get_json(repo: str, path: str) -> tuple:
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return json.loads(content), data["sha"]


def github_put_json(repo: str, path: str, payload: dict,
                    sha: str | None, message: str) -> None:
    content_b64 = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    ).decode("utf-8")
    body = {"message": message, "content": content_b64}
    if sha:
        body["sha"] = sha
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    r = requests.put(url, headers=HEADERS, json=body, timeout=15)
    r.raise_for_status()
    log.info(f"✅ Scritto: {repo}/{path}")


def github_get_sha(repo: str, path: str) -> str | None:
    try:
        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()["sha"]
    except Exception:
        pass
    return None

# ─── RWM SCORE ───────────────────────────────────────────────────────────────
def regime_to_score(regime: str) -> int:
    return {"BULL":5,"ESPANSIONE":12,"NEUTRO":18,"RALLENTAMENTO":24,"BEAR":29}.get(regime.upper(), 18)

def var_to_score(var_pct: float) -> int:
    if var_pct < 2:  return 3
    if var_pct < 5:  return 8
    if var_pct < 10: return 15
    if var_pct < 20: return 22
    return 29

def mifid_to_score(category: str) -> int:
    return {"monetario":2,"obbligaz":8,"hy":13,"real_estate":14,
            "commodity":12,"azionario":18,"leva":20}.get(category, 15)

def shock_to_score(category: str) -> int:
    loss = SHOCK_2008.get(category, 40.0)
    if loss < 5:  return 2
    if loss < 15: return 7
    if loss < 30: return 13
    if loss < 50: return 17
    return 20

def rwm_label(score: int) -> dict:
    if score <= 20: return {"label":"DIFENSIVO", "color":"#16A34A","emoji":"🟢"}
    if score <= 40: return {"label":"MODERATO",  "color":"#EAB308","emoji":"🟡"}
    if score <= 60: return {"label":"BILANCIATO","color":"#F97316","emoji":"🟠"}
    if score <= 80: return {"label":"DINAMICO",  "color":"#DC2626","emoji":"🔴"}
    return             {"label":"AGGRESSIVO","color":"#6D28D9","emoji":"⛔"}

def compute_rwm_score(regime: str, var_pct: float, category: str) -> dict:
    a = regime_to_score(regime)
    b = var_to_score(var_pct)
    c = mifid_to_score(category)
    d = shock_to_score(category)
    total = a + b + c + d
    meta = rwm_label(total)
    return {
        "score": total, "label": meta["label"],
        "color": meta["color"], "emoji": meta["emoji"],
        "components": {
            "regime_macro":a,"var_portafoglio":b,
            "coerenza_mifid":c,"esposizione_shock":d,
        }
    }

# ─── LAYER 2 ─────────────────────────────────────────────────────────────────
def extract_layer2() -> dict:
    layer2 = {}

    try:
        geo_data, _ = github_get_json(REPO_GEO, PATH_GEO)
        for area in ["paesi", "new_area"]:
            for etf in geo_data.get(area, {}).get("qualified", []):
                if etf.get("qualifies") and etf["ticker"] not in layer2:
                    layer2[etf["ticker"]] = {
                        "name":   etf.get("name",""),
                        "source": f"geografia/{area}",
                        "score":  etf.get("score", 0),
                        "signal": etf.get("signal",""),
                        "price":  etf.get("price", 0),
                        "perf7":  etf.get("perf7", 0),
                        "perf30": etf.get("perf30", 0),
                    }
        n_geo = sum(1 for v in layer2.values() if "geografia" in v["source"])
        log.info(f"   → GEOGRAFIA: {n_geo} qualificati")
    except Exception as e:
        log.warning(f"⚠️  GEOGRAFIA: {e}")

    try:
        sett_data, _ = github_get_json(REPO_SETT, PATH_SETT)
        portfolios = sett_data.get("portfolios", sett_data)
        n = 0
        for area_name, area_data in portfolios.items():
            for etf in area_data.get("qualified", []):
                if etf.get("qualifies") and etf["ticker"] not in layer2:
                    layer2[etf["ticker"]] = {
                        "name":   etf.get("name",""),
                        "source": f"settoriali/{area_name}",
                        "score":  etf.get("score", 0),
                        "signal": etf.get("signal",""),
                        "price":  etf.get("price", 0),
                        "perf7":  etf.get("rs_p7", etf.get("perf7", 0)),
                        "perf30": etf.get("rs_p30", etf.get("perf30", 0)),
                    }
                    n += 1
        log.info(f"   → SETTORIALI: {n} qualificati")
    except Exception as e:
        log.warning(f"⚠️  SETTORIALI: {e}")

    try:
        temi_data, _ = github_get_json(REPO_TEMI, PATH_TEMI)
        groups = temi_data.get("groups", temi_data)
        n = 0
        for group_name, group_data in groups.items():
            for etf in group_data.get("qualified", []):
                if etf.get("qualifies") and etf["ticker"] not in layer2:
                    layer2[etf["ticker"]] = {
                        "name":   etf.get("name",""),
                        "source": f"tematici/{group_name}",
                        "score":  etf.get("score", 0),
                        "signal": etf.get("signal",""),
                        "price":  etf.get("price", 0),
                        "perf7":  etf.get("perf7", 0),
                        "perf30": etf.get("perf30", 0),
                    }
                    n += 1
        log.info(f"   → TEMATICI: {n} qualificati")
    except Exception as e:
        log.warning(f"⚠️  TEMATICI: {e}")

    log.info(f"   → LAYER 2 TOTALE: {len(layer2)} ticker unici")
    return layer2

# ─── FETCH PREZZI ────────────────────────────────────────────────────────────
def fetch_etf_data(tickers: list) -> dict:
    log.info(f"⬇️  Scarico {len(tickers)} ETF da yfinance...")
    raw = yf.download(
        tickers, period="1y", interval="1d",
        group_by="ticker", auto_adjust=True,
        progress=False, threads=True
    )
    results = {}
    now_ts = datetime.now(timezone.utc).isoformat()

    for ticker in tickers:
        try:
            close = raw[ticker]["Close"].dropna() if len(tickers) > 1 else raw["Close"].dropna()
            if len(close) < 20:
                results[ticker] = {"ok": False, "ticker": ticker, "ts": now_ts}
                continue

            price    = float(close.iloc[-1])
            price_1w = float(close.iloc[-6])  if len(close) >= 6  else float(close.iloc[0])
            price_1m = float(close.iloc[-22]) if len(close) >= 22 else float(close.iloc[0])
            ytd_idx  = close[close.index.year == datetime.now().year]
            price_ytd= float(ytd_idx.iloc[0]) if len(ytd_idx) > 0 else float(close.iloc[0])

            rets     = close.pct_change().dropna()
            vol_d    = float(rets.std())
            vol_ann  = round(vol_d * np.sqrt(252) * 100, 2)
            mean_d   = float(rets.mean())
            var_95   = round(abs(mean_d - 1.645 * vol_d) * np.sqrt(252) * 100, 2)
            excess   = rets - 0.035 / 252
            sharpe   = round((excess.mean() / excess.std()) * np.sqrt(252), 2) if excess.std() > 0 else 0.0

            results[ticker] = {
                "ok": True, "ticker": ticker,
                "price":     round(price, 4),
                "chg_week":  round((price / price_1w - 1) * 100, 2),
                "chg_month": round((price / price_1m - 1) * 100, 2),
                "chg_ytd":   round((price / price_ytd - 1) * 100, 2),
                "volatility": vol_ann,
                "var_95":     var_95,
                "sharpe_1y":  sharpe,
                "ts":         now_ts,
            }
            log.info(f"  ✓ {ticker:<14} {price:>8.3f}  vol={vol_ann:>5.1f}%  VaR={var_95:>5.1f}%  Sharpe={sharpe:>5.2f}")
        except Exception as e:
            log.warning(f"  ✗ {ticker}: {e}")
            results[ticker] = {"ok": False, "ticker": ticker, "ts": now_ts}

    return results

# ─── CHANGELOG ───────────────────────────────────────────────────────────────
def compute_changelog(prev_universe: dict, new_layer2: dict,
                      layer1_set: set, regime: str, now_it: str) -> dict:
    prev_l2  = {t: v for t, v in prev_universe.get("etf", {}).items() if v.get("layer") == 2}
    prev_set = set(prev_l2.keys())
    new_set  = set(new_layer2.keys()) - layer1_set

    entrati = []
    for t in sorted(new_set - prev_set):
        m = new_layer2[t]
        entrati.append({
            "ticker": t, "name": m.get("name",""),
            "source": m.get("source",""), "score": m.get("score",0),
            "signal": m.get("signal",""), "perf7": m.get("perf7",0),
            "perf30": m.get("perf30",0),
        })

    usciti = []
    for t in sorted(prev_set - new_set):
        m = prev_l2[t]
        usciti.append({
            "ticker": t, "name": m.get("name",""),
            "source": m.get("source",""),
            "motivo": "Non più qualificato dal sistema RAPTOR",
        })

    return {
        "data":      now_it,
        "regime":    regime,
        "n_layer2":  len(new_set),
        "n_entrati": len(entrati),
        "n_usciti":  len(usciti),
        "entrati":   entrati,
        "usciti":    usciti,
    }

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    log.info("═══════════════════════════════════════════════════")
    log.info("  RWM — RAPTOR WEALTH MERIDIAN — ETF Universe Builder")
    log.info("═══════════════════════════════════════════════════")

    now_iso = datetime.now(timezone.utc).isoformat()
    now_it  = datetime.now().strftime("%d/%m/%Y %H:%M")

    # 1. Layer 1
    log.info("📖 Leggo levels.json da compass...")
    levels_data, _ = github_get_json(REPO_COMPASS, PATH_LEVELS)
    tickers_set = set()
    for lv in levels_data.get("levels", []):
        for t in lv.get("etf_pool", []):
            tickers_set.add(t)
    tickers = sorted(tickers_set)
    log.info(f"   → Layer 1: {len(tickers)} ticker")

    # 2. Regime macro
    log.info("📖 Leggo regime macro...")
    rse_data, _  = github_get_json(REPO_RAPTOR, PATH_RSE)
    regime       = rse_data["meta"].get("regime", "NEUTRO")
    regime_score = rse_data["meta"].get("regime_score", 0.0)
    regime_sigs  = rse_data["meta"].get("regime_signals", [])
    log.info(f"   → Regime: {regime} (score {regime_score})")

    # 3. Layer 2
    log.info("🔍 Leggo Layer 2...")
    layer2     = extract_layer2()
    l2_tickers = [t for t in layer2 if t not in tickers_set]
    all_tickers= tickers + l2_tickers
    log.info(f"   → Totale: {len(all_tickers)} ETF ({len(tickers)} L1 + {len(l2_tickers)} L2)")

    # 4. Universe precedente per changelog
    log.info("📖 Leggo universe precedente...")
    try:
        prev_universe, sha_universe = github_get_json(REPO_RWM, PATH_UNIVERSE_RWM)
    except Exception:
        prev_universe = {"etf": {}}
        sha_universe  = None
        log.info("   → Prima esecuzione")

    # 5. Changelog
    log.info("📋 Calcolo changelog...")
    cl = compute_changelog(prev_universe, layer2, tickers_set, regime, now_it)
    log.info(f"   → Entrati: {cl['n_entrati']} | Usciti: {cl['n_usciti']}")
    for e in cl["entrati"]:
        log.info(f"     ✅ ENTRATO: {e['ticker']:<14} {e['source']}  score={e['score']}")
    for u in cl["usciti"]:
        log.info(f"     ❌ USCITO:  {u['ticker']:<14} {u['source']}")

    # 6. Prezzi
    etf_raw = fetch_etf_data(all_tickers)

    # 7. Assembla universe
    log.info("🧮 Calcolo RWM Score...")
    etf_output = {}
    ok_count = err_count = 0

    for ticker in all_tickers:
        raw    = etf_raw.get(ticker, {"ok": False, "ticker": ticker})
        layer  = 2 if ticker in layer2 else 1
        l2m    = layer2.get(ticker, {})

        if not raw.get("ok"):
            etf_output[ticker] = {
                "ok": False, "ticker": ticker, "layer": layer,
                "category": ETF_CATEGORY.get(ticker, "azionario"),
                "source":   l2m.get("source", "levels"),
                "name":     l2m.get("name", ""),
                "ts":       now_iso,
            }
            err_count += 1
            continue

        cat = ETF_CATEGORY.get(ticker, "azionario")
        rwm = compute_rwm_score(regime, raw["var_95"], cat)

        etf_output[ticker] = {
            "ok":             True,
            "ticker":         ticker,
            "layer":          layer,
            "category":       cat,
            "source":         l2m.get("source", "levels"),
            "name":           l2m.get("name", ""),
            "price":          raw["price"],
            "chg_week":       raw["chg_week"],
            "chg_month":      raw["chg_month"],
            "chg_ytd":        raw["chg_ytd"],
            "volatility":     raw["volatility"],
            "var_95":         raw["var_95"],
            "sharpe_1y":      raw["sharpe_1y"],
            "rwm_score":      rwm["score"],
            "rwm_label":      rwm["label"],
            "rwm_color":      rwm["color"],
            "rwm_emoji":      rwm["emoji"],
            "rwm_components": rwm["components"],
            "raptor_score":   l2m.get("score"),
            "raptor_signal":  l2m.get("signal"),
            "ts":             raw["ts"],
        }
        ok_count += 1

    universe = {
        "meta": {
            "updated":        now_iso,
            "updated_it":     now_it,
            "source":         "yfinance + RAPTOR Suite",
            "n_layer1":       len(tickers),
            "n_layer2":       len(l2_tickers),
            "n_etf_total":    len(all_tickers),
            "n_etf_ok":       ok_count,
            "n_etf_error":    err_count,
            "regime":         regime,
            "regime_score":   regime_score,
            "regime_signals": regime_sigs,
        },
        "etf": etf_output
    }

    # 8. Aggiorna changelog storico
    log.info("📋 Aggiorno etf_changelog.json...")
    try:
        changelog_data, sha_changelog = github_get_json(REPO_RWM, PATH_CHANGELOG_RWM)
    except Exception:
        changelog_data = {"entries": []}
        sha_changelog  = None

    is_monday   = datetime.now().weekday() == 0
    has_changes = cl["n_entrati"] > 0 or cl["n_usciti"] > 0

    if has_changes or is_monday:
        changelog_data["entries"].insert(0, cl)
        changelog_data["entries"]     = changelog_data["entries"][:365]
        changelog_data["last_updated"]= now_it
        changelog_data["total_entries"]= len(changelog_data["entries"])
        github_put_json(
            REPO_RWM, PATH_CHANGELOG_RWM, changelog_data, sha_changelog,
            f"📋 Changelog {now_it} +{cl['n_entrati']} -{cl['n_usciti']}"
        )
    else:
        log.info("   → Nessuna variazione, changelog invariato")

    # 9. Scrive universe su RWM
    github_put_json(
        REPO_RWM, PATH_UNIVERSE_RWM, universe, sha_universe,
        f"📊 ETF Universe {now_it} L1:{len(tickers)} L2:{len(l2_tickers)}"
    )

    # 10. Copia universe su compass
    sha_compass = github_get_sha(REPO_COMPASS, PATH_UNIVERSE_COMPASS)
    github_put_json(
        REPO_COMPASS, PATH_UNIVERSE_COMPASS, universe, sha_compass,
        f"📊 ETF Universe sync {now_it}"
    )

    log.info("═══════════════════════════════════════════════════")
    log.info(f"  DONE  {ok_count}/{len(all_tickers)} ETF | L1:{len(tickers)} L2:{len(l2_tickers)}")
    log.info(f"  Regime:{regime}  Entrati:{cl['n_entrati']}  Usciti:{cl['n_usciti']}")
    log.info("═══════════════════════════════════════════════════")


if __name__ == "__main__":
    main()
