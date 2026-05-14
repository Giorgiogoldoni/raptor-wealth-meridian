#!/usr/bin/env python3
"""
fetch_rwm.py v2 — RWM ETF Universe Builder
AUTONOMO — zero dipendenze da repo esterni privati.
Secrets: solo GITHUB_TOKEN standard.
"""

import os, json, base64, logging, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import requests

try:
    import yfinance as yf
except ImportError:
    os.system("pip install yfinance --break-system-packages -q")
    import yfinance as yf

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("fetch_rwm")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO_RWM     = "Giorgiogoldoni/raptor-wealth-meridian"
HEADERS      = {"Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json"}

PATH_LEVELS         = "data/levels.json"
PATH_UNIVERSE       = "data/etf_universe_rwm.json"
PATH_CHANGELOG      = "data/etf_changelog.json"
PATH_REGIME_HISTORY = "data/regime_history.json"
PATH_PORTAFOGLI_DIR = "data/portafogli"

LAYER2_SOURCES = {
    "geografia":  "https://giorgiogoldoni.github.io/raptor-geografia/geografia.json",
    "settoriali": "https://giorgiogoldoni.github.io/raptor-settoriali/settoriali.json",
    "tematici":   "https://giorgiogoldoni.github.io/raptor-tematici/tematici.json",
}

REGIME_TICKERS = {
    "vix":     "^VIX",     "sp500":   "^GSPC",
    "us10y":   "^TNX",     "us2y":    "^IRX",
    "stoxx50": "^STOXX50E","ibtm":    "IBTM.MI",
    "sxrm":    "SXRM.MI",  "nikkei":  "^N225",
    "hsi":     "^HSI",     "eem":     "EEM",
    "ihyu":    "IHYU.MI",  "aggh":    "AGGH.MI",
    "embe":    "EMBE.MI",  "eurusd":  "EURUSD=X",
}

SCENARI = {
    "2008":       {"start":"2008-07-01","end":"2009-02-28","label":"Crisi 2008"},
    "covid":      {"start":"2020-02-19","end":"2020-03-23","label":"Covid 2020"},
    "inflazione": {"start":"2022-01-03","end":"2022-10-13","label":"Inflazione 2022"},
}

# ── GITHUB ────────────────────────────────────────────────────────────────────
def github_get(path):
    url = f"https://api.github.com/repos/{REPO_RWM}/contents/{path}"
    r   = requests.get(url, headers=HEADERS, timeout=15)
    if r.status_code == 404: return None, None
    r.raise_for_status()
    d = r.json()
    return json.loads(base64.b64decode(d["content"]).decode()), d["sha"]

def github_put(path, payload, sha, message):
    b64 = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, indent=2).encode()).decode()
    body = {"message": message, "content": b64}
    if sha: body["sha"] = sha
    r = requests.put(f"https://api.github.com/repos/{REPO_RWM}/contents/{path}",
                     headers=HEADERS, json=body, timeout=15)
    r.raise_for_status()
    log.info(f"✅ {path}")

def fetch_url(url):
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent":"RWM/2.0"})
        r.raise_for_status(); return r.json()
    except Exception as e:
        log.warning(f"⚠️  {url}: {e}"); return None

# ── HELPERS ───────────────────────────────────────────────────────────────────
def mom(prices, days=20):
    if len(prices) < days+1: return None
    return (float(prices[-1])/float(prices[-(days+1)])-1)*100

def vol_ann(prices, days=20):
    if len(prices) < days+1: return None
    rets = np.diff(np.log(np.array(prices[-days:], dtype=float)))
    return float(np.std(rets)*np.sqrt(252)*100)

def adaptive(value, history, inverse=False):
    if value is None or not history or len(history) < 5: return 50.0
    arr  = np.array(history[-60:], dtype=float)
    mean = float(np.mean(arr)); std = float(np.std(arr)) or 1.0
    z    = (value-mean)/std
    if inverse: z = -z
    return max(0.0, min(100.0, 50+z*15))

def rlabel(score):
    if score >= 80: return "BULL"
    if score >= 60: return "ESPANSIONE"
    if score >= 40: return "NEUTRO"
    if score >= 20: return "RALLENTAMENTO"
    return "BEAR"

def rlevel(score):
    if score >= 80: return 9
    if score >= 65: return 7
    if score >= 50: return 5
    if score >= 35: return 3
    return 1

# ── REGIME MACRO ──────────────────────────────────────────────────────────────
def scarica_regime():
    log.info("📡 Scarico proxy regime...")
    tks = list(REGIME_TICKERS.values())
    raw = {}
    try:
        hist = yf.download(tks, period="90d", interval="1d",
                           group_by="ticker", auto_adjust=True,
                           progress=False, threads=True)
        for name, tk in REGIME_TICKERS.items():
            try:
                closes = (hist[tk]["Close"] if len(tks)>1 else hist["Close"]).dropna().values.tolist()
                raw[name] = closes
                log.info(f"  ✓ {name}: {len(closes)} barre")
            except:
                raw[name] = []
    except Exception as e:
        log.error(f"Download regime: {e}")
    return raw

def regime_usa(raw, vh):
    vix  = raw.get("vix",[]);   sp = raw.get("sp500",[])
    t10  = raw.get("us10y",[]); t2 = raw.get("us2y",[])
    vn   = float(vix[-1]) if vix else 20.0
    mn   = mom(sp, 20)
    crv  = (float(t10[-1])-float(t2[-1])) if t10 and t2 else 0.5
    sv   = adaptive(vn, vh.get("vix_h",[]), inverse=True)
    sm   = adaptive(mn or 0, vh.get("sp_mom_h",[]))
    sc   = adaptive(crv, vh.get("crv_h",[]))
    score= sv*0.40+sm*0.40+sc*0.20
    return {"signal":rlabel(score),"score":round(score,1),
            "vix":round(vn,2),"momentum_sp500_20d":round(mn,2) if mn else None,
            "curve_us_2y10y":round(crv,3)}

def regime_eu(raw, vh):
    sx  = raw.get("stoxx50",[]); ib = raw.get("ibtm",[]); sm = raw.get("sxrm",[])
    ve  = vol_ann(sx, 20); me = mom(sx, 20)
    spr = None
    if ib and sm and len(ib)>20 and len(sm)>20:
        spr = (float(ib[-1])/float(ib[-21])-float(sm[-1])/float(sm[-21]))*100
    sv  = adaptive(ve or 20, vh.get("eu_vol_h",[]), inverse=True)
    smo = adaptive(me or 0, vh.get("eu_mom_h",[]))
    ssp = adaptive(spr or 0, vh.get("eu_spr_h",[]), inverse=True)
    score = sv*0.30+smo*0.45+ssp*0.25
    return {"signal":rlabel(score),"score":round(score,1),
            "vol_stoxx50_20d":round(ve,2) if ve else None,
            "momentum_stoxx50_20d":round(me,2) if me else None,
            "spread_btp_bund_proxy":round(spr,3) if spr else None}

def regime_asia(raw, vh):
    nk = raw.get("nikkei",[]); hs = raw.get("hsi",[])
    vn = vol_ann(nk,20); mn = mom(nk,20); mh = mom(hs,20)
    sv = adaptive(vn or 20, vh.get("as_vol_h",[]), inverse=True)
    sn = adaptive(mn or 0, vh.get("as_momn_h",[]))
    sh = adaptive(mh or 0, vh.get("as_momh_h",[]))
    score = sv*0.25+sn*0.40+sh*0.35
    return {"signal":rlabel(score),"score":round(score,1),
            "vol_nikkei_20d":round(vn,2) if vn else None,
            "momentum_nikkei_20d":round(mn,2) if mn else None,
            "momentum_hsi_20d":round(mh,2) if mh else None}

def regime_em(raw, vh):
    em = raw.get("eem",[])
    me = mom(em,20); ve = vol_ann(em,20)
    sm = adaptive(me or 0, vh.get("em_mom_h",[]))
    sv = adaptive(ve or 20, vh.get("em_vol_h",[]), inverse=True)
    score = sm*0.60+sv*0.40
    return {"signal":rlabel(score),"score":round(score,1),
            "momentum_em_20d":round(me,2) if me else None,
            "vol_em_20d":round(ve,2) if ve else None}

def regime_globale(usa, eu, asia, em):
    score = usa["score"]*0.40+eu["score"]*0.30+asia["score"]*0.20+em["score"]*0.10
    return {"signal":rlabel(score),"score":round(score,1),
            "livello_suggerito":rlevel(score)}

def regime_bond(raw, vh):
    t10=raw.get("us10y",[]); t2=raw.get("us2y",[])
    iy=raw.get("ihyu",[]); ag=raw.get("aggh",[]); em=raw.get("embe",[])
    eu=raw.get("eurusd",[])

    crv = round(float(t10[-1])-float(t2[-1]),3) if t10 and t2 else None
    if crv is None:    cs="sconosciuto"
    elif crv < -0.1:   cs="invertita"
    elif crv < 0.5:    cs="piatta"
    elif crv < 1.5:    cs="normale"
    else:              cs="ripida"

    spr_hy = None
    if iy and ag and len(iy)>20 and len(ag)>20:
        spr_hy = round((float(iy[-1])/float(iy[-21])-float(ag[-1])/float(ag[-21]))*100,3)
    s_hy = adaptive(spr_hy or 0, vh.get("hy_spr_h",[]))
    hs   = "restringimento" if s_hy>=55 else "stabile" if s_hy>=40 else "allargamento"

    spr_em = None
    if em and ag and len(em)>20 and len(ag)>20:
        spr_em = round((float(em[-1])/float(em[-21])-float(ag[-1])/float(ag[-21]))*100,3)
    s_em = adaptive(spr_em or 0, vh.get("em_spr_h",[]))
    es   = "positivo" if s_em>=55 else "neutro" if s_em>=40 else "attenzione"

    usd_m = round((float(eu[-21])/float(eu[-1])-1)*100,2) if eu and len(eu)>20 else None
    us    = "forte" if (usd_m or 0)>1.5 else "stabile" if (usd_m or 0)>0 else "debole"

    dur_r = "corta" if cs in ("invertita","piatta") else "lunga" if cs=="ripida" else "media"
    crd_r = "solo_IG" if hs=="allargamento" else "IG_e_HY" if hs=="restringimento" else "IG_preferito"
    val_r = "hedged" if us=="forte" else "mix"
    em_r  = "riduci" if es=="attenzione" else "mantieni"

    alloc = {
        "corta":  {"monetario":20,"gov_breve":20,"gov_lungo":5,
                   "hy":0 if crd_r=="solo_IG" else 5,"em_bond":0},
        "media":  {"monetario":10,"gov_breve":10,"gov_lungo":15,
                   "hy":5 if crd_r=="solo_IG" else 10,
                   "em_bond":0 if em_r=="riduci" else 5},
        "lunga":  {"monetario":5,"gov_breve":5,"gov_lungo":25,
                   "hy":5 if crd_r=="solo_IG" else 10,
                   "em_bond":0 if em_r=="riduci" else 5},
    }[dur_r]

    return {
        "curve_us_2y10y":crv,"curve_us_signal":cs,
        "spread_hy":spr_hy,"spread_hy_signal":hs,
        "spread_em":spr_em,"spread_em_signal":es,
        "usd_momentum":usd_m,"usd_signal":us,
        "raccomandazione":{
            "duration":dur_r,"credito":crd_r,
            "valuta":val_r,"em_bond":em_r,
            "allocation_bond":alloc,
        }
    }

# ── LAYER 2 ───────────────────────────────────────────────────────────────────
def extract_layer2():
    layer2 = {}
    for src, url in LAYER2_SOURCES.items():
        data = fetch_url(url)
        if not data: continue
        n = 0
        try:
            if src == "geografia":
                for area, group in data.items():
                    if not isinstance(group, dict): continue
                    for item in group.get("all", group.get("qualified",[])):
                        tk = item.get("ticker","")
                        if tk and tk not in layer2:
                            layer2[tk] = {"name":item.get("name",""),
                                "source":f"geografia/{area}",
                                "score":item.get("score",0),
                                "signal":item.get("buy_level",item.get("signal",""))}
                            n += 1
            elif src == "settoriali":
                for pn, port in data.get("portfolios",data).items():
                    for item in port.get("qualified",[]):
                        tk = item.get("ticker","")
                        if tk and tk not in layer2:
                            layer2[tk] = {"name":item.get("name",""),
                                "source":f"settoriali/{pn}",
                                "score":item.get("score",0),
                                "signal":item.get("signal","")}
                            n += 1
            elif src == "tematici":
                for gn, group in data.get("groups",data).items():
                    for item in group.get("qualified",[]):
                        tk = item.get("ticker","")
                        if tk and tk not in layer2:
                            layer2[tk] = {"name":item.get("name",""),
                                "source":f"tematici/{gn}",
                                "score":item.get("score",0),
                                "signal":item.get("buy_level",item.get("signal",""))}
                            n += 1
            log.info(f"   → {src}: {n} ETF")
        except Exception as e:
            log.warning(f"   ✗ {src}: {e}")
    log.info(f"   → LAYER 2: {len(layer2)} unici")
    return layer2

# ── FETCH PREZZI + DIVIDENDI + SCENARI ───────────────────────────────────────
def fetch_etf_data(tickers, profiles):
    log.info(f"⬇️  Scarico {len(tickers)} ETF...")
    now_ts = datetime.now(timezone.utc).isoformat()

    raw = yf.download(tickers, period="1y", interval="1d",
                      group_by="ticker", auto_adjust=True,
                      progress=False, threads=True)
    results = {}

    for tk in tickers:
        try:
            cl = (raw[tk]["Close"] if len(tickers)>1 else raw["Close"]).dropna()
            if len(cl) < 20:
                results[tk] = {"ok":False,"ticker":tk,"ts":now_ts}; continue

            px   = cl.values
            p    = float(px[-1])
            p1w  = float(px[-6])  if len(px)>=6  else p
            p1m  = float(px[-22]) if len(px)>=22 else p
            p3m  = float(px[-63]) if len(px)>=63 else p
            p6m  = float(px[-126])if len(px)>=126 else p
            p1y  = float(px[0])
            ytd  = cl[cl.index.year==datetime.now().year]
            pytd = float(ytd.iloc[0]) if len(ytd)>0 else p

            rets  = np.diff(np.log(px))
            vol_d = float(np.std(rets))
            vol_a = round(vol_d*np.sqrt(252)*100, 2)
            mean_d= float(np.mean(rets))
            var95 = round(abs(mean_d-1.645*vol_d)*np.sqrt(252)*100, 2)
            shrp  = round((np.mean(rets-0.035/252)/vol_d)*np.sqrt(252),2) if vol_d>0 else 0.0
            peak  = np.maximum.accumulate(px)
            mxdd  = round(float(np.min((px-peak)/peak))*100, 2)

            # Dividendi
            div_yield = div_ttm = None
            try:
                obj  = yf.Ticker(tk)
                divs = obj.dividends
                if len(divs) > 0:
                    cutoff   = divs.index[-1]-timedelta(days=365)
                    div_ttm  = round(float(divs[divs.index>=cutoff].sum()),4)
                    div_yield= round(div_ttm/p*100, 2) if p>0 else None
            except: pass

            # Rendimento totale approssimato (prezzo + dividendi)
            total_return_1y = round((p/p1y-1)*100 + (div_yield or 0), 2)

            # Scenari storici
            scenari = {}
            try:
                obj = yf.Ticker(tk)
                for sn, si in SCENARI.items():
                    hs = obj.history(start=si["start"], end=si["end"], auto_adjust=True)
                    if len(hs) > 5:
                        p0 = float(hs["Close"].iloc[0])
                        p1 = float(hs["Close"].iloc[-1])
                        scenari[sn] = {
                            "drawdown_pct": round((p1/p0-1)*100, 2),
                            "label": si["label"],
                        }
            except: pass

            # Profilo bond
            prof = profiles.get(tk, {})
            cat  = prof.get("categoria","azionario")
            bp   = None
            if cat in ("obbligaz","hy","monetario"):
                bp = {
                    "duration_class": prof.get("duration_class"),
                    "duration_years": prof.get("duration_years"),
                    "credit_quality": prof.get("credit_quality"),
                    "currency_hedge": prof.get("currency_hedge",False),
                    "geographic":     prof.get("geo"),
                    "regime_ok":      True,
                    "regime_warning": None,
                }

            results[tk] = {
                "ok":True,"ticker":tk,
                # Identificazione
                "categoria":      cat,
                "distribuzione":  prof.get("distribuzione","Acc"),
                "geo":            prof.get("geo",""),
                # Prezzo
                "price":          round(p,4),
                # Performance
                "chg_1w":         round((p/p1w-1)*100,2),
                "chg_1m":         round((p/p1m-1)*100,2),
                "chg_3m":         round((p/p3m-1)*100,2),
                "chg_6m":         round((p/p6m-1)*100,2),
                "chg_ytd":        round((p/pytd-1)*100,2),
                "chg_1y":         round((p/p1y-1)*100,2),
                "total_return_1y":total_return_1y,
                # Rischio
                "volatility_1y":  vol_a,
                "var_95_1y":      var95,
                "sharpe_1y":      shrp,
                "max_dd_1y":      mxdd,
                # Reddito
                "div_yield":      div_yield,
                "dividends_ttm":  div_ttm,
                # Bond profile
                "bond_profile":   bp,
                # Scenari stress test
                "scenari":        scenari,
                "ts":             now_ts,
            }
            log.info(f"  ✓ {tk:<14} {p:>8.3f}  1y={round((p/p1y-1)*100,1):>+5.1f}%"
                     f"  vol={vol_a:>5.1f}%  div={div_yield or 0:>4.1f}%")
            time.sleep(0.08)
        except Exception as e:
            log.warning(f"  ✗ {tk}: {e}")
            results[tk] = {"ok":False,"ticker":tk,"ts":now_ts}

    return results

# ── NAV PORTAFOGLI ────────────────────────────────────────────────────────────
def aggiorna_nav(etf_data, now_str, now_it, rg):
    log.info("📈 Aggiorno NAV portafogli...")
    level_ids = [f"C{i}" for i in range(1,10)]+[f"A{i}" for i in range(1,10)]
    for lid in level_ids:
        path = f"{PATH_PORTAFOGLI_DIR}/{lid}.json"
        ptf, sha = github_get(path)
        if not ptf or not ptf.get("posizioni"): continue

        rend = 0.0; n_ok = 0
        for pos in ptf["posizioni"]:
            tk  = pos.get("ticker","")
            peso= pos.get("peso_target",0)/100
            pc  = pos.get("prezzo_carico",0)
            e   = etf_data.get(tk,{})
            if not e.get("ok") or not pc or pc<=0: continue
            p = e.get("price",0)
            if p>0:
                rend += (p/pc-1)*peso; n_ok += 1

        nav = round(100.0*(1+rend), 4)
        today = now_str[:10]
        storico = [s for s in ptf.get("storico_nav",[]) if s.get("data","")[:10]!=today]
        storico.append({"data":today,"nav":nav,
                        "regime":rg.get("signal","NEUTRO"),
                        "rend_pct":round(rend*100,4)})
        ptf["storico_nav"] = storico[-730:]
        ptf["nav_attuale"] = nav
        ptf["nav_updated"] = now_str

        for uso in ptf.get("utilizzi",[]):
            ni = uso.get("nav_ingresso",100.0)
            im = uso.get("importo",0)
            if ni>0 and im>0:
                v = round(im*nav/ni,2)
                uso.update({"valore_attuale":v,
                            "rendimento_pct":round((nav/ni-1)*100,2),
                            "rendimento_eur":round(v-im,2),
                            "aggiornato":now_str})

        github_put(path, ptf, sha, f"📈 NAV {lid} {now_it} → {nav}")
        log.info(f"   ✓ {lid}: NAV={nav} ({n_ok} posizioni)")

# ── STORICO REGIME ────────────────────────────────────────────────────────────
def aggiorna_history(rdata, raw, now_str):
    history, sha = github_get(PATH_REGIME_HISTORY)
    if not history: history = {"entries":[],"value_histories":{}}

    today   = now_str[:10]
    entries = [e for e in history.get("entries",[]) if e.get("data","")[:10]!=today]
    entries.append({"data":today,**rdata})
    entries = entries[-365:]
    history["entries"] = entries

    curr = rdata.get("globale",{}).get("signal","NEUTRO")
    days = sum(1 for e in reversed(entries)
               if e.get("globale",{}).get("signal")==curr)
    history.update({"current_regime":curr,"days_in_current_regime":days,
                    "updated":now_str})

    vh = history.get("value_histories",{})
    def apv(k, v):
        if v is not None:
            vh.setdefault(k,[]).append(round(float(v),4))
            vh[k] = vh[k][-90:]

    vix=raw.get("vix",[]); sp=raw.get("sp500",[])
    t10=raw.get("us10y",[]); t2=raw.get("us2y",[])
    sx=raw.get("stoxx50",[]); nk=raw.get("nikkei",[])
    em=raw.get("eem",[]); iy=raw.get("ihyu",[])
    ag=raw.get("aggh",[]); eb=raw.get("embe",[])
    eu=raw.get("eurusd",[])

    apv("vix_h",       vix[-1] if vix else None)
    apv("sp_mom_h",    mom(sp,20))
    apv("crv_h",       (float(t10[-1])-float(t2[-1])) if t10 and t2 else None)
    apv("eu_vol_h",    vol_ann(sx,20))
    apv("eu_mom_h",    mom(sx,20))
    apv("as_vol_h",    vol_ann(nk,20))
    apv("as_momn_h",   mom(nk,20))
    apv("em_mom_h",    mom(em,20))
    apv("em_vol_h",    vol_ann(em,20))
    if iy and ag and len(iy)>20 and len(ag)>20:
        apv("hy_spr_h",(float(iy[-1])/float(iy[-21])-float(ag[-1])/float(ag[-21]))*100)
    if eb and ag and len(eb)>20 and len(ag)>20:
        apv("em_spr_h",(float(eb[-1])/float(eb[-21])-float(ag[-1])/float(ag[-21]))*100)
    if eu and len(eu)>20:
        apv("usd_mom_h",(float(eu[-21])/float(eu[-1])-1)*100)

    history["value_histories"] = vh
    github_put(PATH_REGIME_HISTORY, history, sha, f"📊 Regime {today}")
    return history

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    now    = datetime.now(timezone.utc)
    now_s  = now.isoformat()
    now_it = now.strftime("%d/%m/%Y %H:%M")

    log.info("═"*55)
    log.info("  RWM v2 — RAPTOR WEALTH MERIDIAN")
    log.info("═"*55)

    # 1. levels.json locale
    with open(PATH_LEVELS) as f: ld = json.load(f)
    levels   = ld.get("levels",[])
    profiles = ld.get("etf_profiles",{})
    tk_set   = {tk for lv in levels for tk in lv.get("etf_pool",[])}
    tickers  = sorted(tk_set)
    log.info(f"📖 Layer 1: {len(tickers)} ticker")

    # 2. Storico per adaptive scoring
    hist_data,_ = github_get(PATH_REGIME_HISTORY)
    vh = (hist_data or {}).get("value_histories",{})

    # 3. Regime macro
    raw   = scarica_regime()
    r_usa = regime_usa(raw, vh)
    r_eu  = regime_eu(raw, vh)
    r_as  = regime_asia(raw, vh)
    r_em  = regime_em(raw, vh)
    r_gl  = regime_globale(r_usa, r_eu, r_as, r_em)
    r_bd  = regime_bond(raw, vh)

    log.info(f"🌍 Regime: {r_gl['signal']} (score {r_gl['score']}) "
             f"→ livello {r_gl['livello_suggerito']}")
    log.info(f"🔗 Bond: duration={r_bd['raccomandazione']['duration']} "
             f"credito={r_bd['raccomandazione']['credito']}")

    rdata = {"globale":r_gl,"usa":r_usa,"eu":r_eu,"asia":r_as,"em":r_em,"bond":r_bd}

    # 4. Layer 2
    layer2     = extract_layer2()
    l2_tickers = [t for t in layer2 if t not in tk_set]
    all_tickers= tickers + l2_tickers
    log.info(f"📦 Totale: {len(all_tickers)} ETF ({len(tickers)} L1 + {len(l2_tickers)} L2)")

    # 5. Universe precedente
    prev, sha_u = github_get(PATH_UNIVERSE)
    if not prev: prev = {"etf":{}}; sha_u = None

    # 6. Prezzi + dividendi
    etf_raw = fetch_etf_data(all_tickers, profiles)

    # 7. Bond regime check per ogni ETF
    rec = r_bd["raccomandazione"]
    for tk, e in etf_raw.items():
        if not e.get("ok") or not e.get("bond_profile"): continue
        bp = e["bond_profile"]
        ok = True; warn = None
        if bp.get("duration_class")=="lunga" and rec["duration"]=="corta":
            ok=False; warn="Duration elevata — tassi in risalita"
        elif bp.get("credit_quality") in ("HY","GOV_EM") and rec["credito"]=="solo_IG":
            ok=False; warn="Credito rischioso — spread in allargamento"
        elif not bp.get("currency_hedge") and rec["valuta"]=="hedged":
            warn="Valuta non hedged — USD forte"
        bp["regime_ok"]=ok; bp["regime_warning"]=warn

    # 8. Assembla universe
    log.info("🧮 Assemblo universe...")
    etf_out = {}; ok_n = err_n = 0
    for tk in all_tickers:
        e    = etf_raw.get(tk,{"ok":False,"ticker":tk})
        prof = profiles.get(tk,{})
        lv_ids=[lv["id"] for lv in levels if tk in lv.get("etf_pool",[])]
        l2m  = layer2.get(tk,{})
        layer= 2 if tk in layer2 else 1

        if not e.get("ok"):
            etf_out[tk]={"ok":False,"ticker":tk,"layer":layer,
                "categoria":prof.get("categoria","azionario"),
                "distribuzione":prof.get("distribuzione","Acc"),
                "geo":prof.get("geo",""),"livelli":lv_ids,
                "source":l2m.get("source","levels"),"ts":now_s}
            err_n+=1; continue

        etf_out[tk] = {
            "ok":True,"ticker":tk,"layer":layer,
            # Classificazione
            "categoria":      e["categoria"],
            "distribuzione":  e["distribuzione"],
            "geo":            e["geo"],
            "livelli":        lv_ids,
            "source":         l2m.get("source","levels"),
            # Prezzo
            "price":          e["price"],
            # Performance
            "chg_1w":         e["chg_1w"],
            "chg_1m":         e["chg_1m"],
            "chg_3m":         e["chg_3m"],
            "chg_6m":         e["chg_6m"],
            "chg_ytd":        e["chg_ytd"],
            "chg_1y":         e["chg_1y"],
            "total_return_1y":e["total_return_1y"],
            # Rischio
            "volatility_1y":  e["volatility_1y"],
            "var_95_1y":      e["var_95_1y"],
            "sharpe_1y":      e["sharpe_1y"],
            "max_dd_1y":      e["max_dd_1y"],
            # Reddito
            "div_yield":      e["div_yield"],
            "dividends_ttm":  e["dividends_ttm"],
            # Bond
            "bond_profile":   e["bond_profile"],
            # Stress test
            "scenari":        e["scenari"],
            # Layer 2
            "raptor_score":   l2m.get("score"),
            "raptor_signal":  l2m.get("signal"),
            "ts":             e["ts"],
        }
        ok_n += 1

    universe = {
        "meta": {
            "updated":now_s,"updated_it":now_it,
            "source":"yfinance autonomo — RWM v2",
            "n_layer1":len(tickers),"n_layer2":len(l2_tickers),
            "n_etf_total":len(all_tickers),
            "n_etf_ok":ok_n,"n_etf_error":err_n,
        },
        "regime": rdata,
        "etf": etf_out,
    }

    # 9. Changelog
    cl_data, sha_cl = github_get(PATH_CHANGELOG)
    if not cl_data: cl_data={"entries":[]}; sha_cl=None
    prev_l2 = {t for t,v in prev.get("etf",{}).items() if v.get("layer")==2}
    new_l2  = set(layer2.keys())-tk_set
    entrati = [{"ticker":t,"source":layer2[t].get("source","")}
               for t in sorted(new_l2-prev_l2)]
    usciti  = [{"ticker":t} for t in sorted(prev_l2-new_l2)]
    if entrati or usciti or now.weekday()==0:
        cl_data["entries"].insert(0,{
            "data":now_it,"regime":r_gl["signal"],
            "n_entrati":len(entrati),"n_usciti":len(usciti),
            "entrati":entrati,"usciti":usciti,
        })
        cl_data["entries"] = cl_data["entries"][:365]
        github_put(PATH_CHANGELOG, cl_data, sha_cl,
                   f"📋 Changelog {now_it}")

    # 10. Scrivi universe
    github_put(PATH_UNIVERSE, universe, sha_u,
               f"📊 ETF Universe {now_it} L1:{len(tickers)} L2:{len(l2_tickers)}")

    # 11. Storico regime
    aggiorna_history(rdata, raw, now_s)

    # 12. NAV portafogli
    aggiorna_nav(etf_out, now_s, now_it, r_gl)

    log.info("═"*55)
    log.info(f"  DONE  {ok_n}/{len(all_tickers)} ETF ok")
    log.info(f"  Regime: {r_gl['signal']} score={r_gl['score']}")
    log.info(f"  Bond: {r_bd['raccomandazione']['duration']} / "
             f"{r_bd['raccomandazione']['credito']}")
    log.info("═"*55)

if __name__ == "__main__":
    main()
