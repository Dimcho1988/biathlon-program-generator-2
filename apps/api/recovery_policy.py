"""Measured loading blocks and independent recovery evidence.

Thresholds describe an explicit coaching policy, not a FOR/NFOR diagnosis.
No forecast, load-derived Recovery value or planned accent becomes an outcome.
"""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
from math import log
from statistics import median

from biathlon import load_evidence, individual_learning
from biathlon.constants import COMPONENTS
from biathlon.learning_provenance import eligible_load_rows, heuristic_load_dates
from biathlon.load_progression import observed_window
from . import learning_evidence as legacy
from .response_monitoring import number, relative_score
from .stress_model import CHANNELS

VERSION = "observed-block-recovery-v1"
INDEPENDENT = {k for k, (family, _) in CHANNELS.items() if family != "functional"}
CORE = {"hrv", "resting_hr", "sleep_duration", "fatigue", "sleep_quality", "stress", "soreness", "motivation", "competition_motivation", "morning_weight"}


def recovery_limit(loading_days):
    if type(loading_days) is not int or not 7 <= loading_days <= 21:
        return None
    return 3.+2.*(loading_days-7)/7.


def proposed_step(loading_days, recovery_days, confidence=1.):
    """Two policy branches. The output is a candidate, never permission."""
    limit = recovery_limit(loading_days)
    if limit is None or number(recovery_days) is None or recovery_days < 0:
        return None
    ratio = recovery_days/limit
    return .20*max(0., min(1., confidence))*max(0., min(1., 1-ratio)) if ratio < 1 else -.20*max(0., min(1., 2*(ratio-1))) or 0.


def unidentified_activity_count(source):
    quality=source.get("quality") or {}
    counted=sum(int(quality.get(key) or 0) for key in ("limited_activities","excluded_activities"))
    identified=0
    undated=0
    for activity in source.get("activities",[]):
        proof=load_evidence.assess(activity,unified=bool(source.get("load_selection")))
        problematic=activity.get("quality_status","valid")!="valid" or not proof["complete"]
        if problematic:
            if activity.get("date") or activity.get("local_date"):
                identified+=1
            else:
                undated+=1
    return max(undated,counted-identified,0)


def measured_rows(source, rows):
    """Invalidate affected DATES, not an entire otherwise usable history."""
    excluded = set(heuristic_load_dates(source))
    for activity in source.get("activities", []):
        proof = load_evidence.assess(activity, unified=bool(source.get("load_selection")))
        if not proof["complete"] or activity.get("planning_estimated"):
            excluded.add(activity.get("date") or activity.get("local_date"))
    # A missing catalogue activity must not silently become a covered rest day.
    for issue in (source.get("quality") or {}).get("exclusions", []):
        if isinstance(issue, dict) and (issue.get("date") or issue.get("local_date")):
            excluded.add(issue.get("date") or issue.get("local_date"))
    if unidentified_activity_count(source):
        return [], sorted(d for d in excluded if d)
    aerobic_dates={a.get("date") or a.get("local_date") for a in source.get("activities",[]) if not load_evidence.is_strength(a)}
    for row in rows:
        if row.get("zone")!="STR" and number(row.get("effective_load")) is not None and row["effective_load"]>0 and row.get("date") not in aerobic_dates:
            excluded.add(row.get("date"))
    result = eligible_load_rows(source, rows)
    counts = {}
    for row in result:
        key = (row.get("date"), row.get("zone"))
        counts[key] = counts.get(key, 0)+1
    for row in result:
        if counts[(row.get("date"), row.get("zone"))] != 1 or number(row.get("effective_load")) is None or row["effective_load"] < 0:
            excluded.add(row.get("date"))
    return [r for r in result if r.get("date") not in excluded], sorted(d for d in excluded if d)


def _daily(source, rows, first, last, component=None):
    result = {}
    for i in range(max(0, (last-first).days+1)):
        day = first+timedelta(days=i)
        window = observed_window(source, rows, day, day+timedelta(days=1))
        values = [window["components"][z].get("weekly_q") for z in ([component] if component else COMPONENTS)]
        result[day.isoformat()] = sum(values)/7 if window["complete"] and all(number(v) is not None for v in values) else None
    return result


def _mean(daily, start, end):
    values = [daily.get((start+timedelta(days=i)).isoformat()) for i in range((end-start).days+1)]
    return sum(values)/len(values) if values and all(number(v) is not None for v in values) else None


def _detect_one_curve(daily, selected, today):
    """Confirm a three-day actual downshift; infer onset only from an observed rise.

    A manual BLOCK only identifies candidate boundaries. Both its loading dose
    and its lower follow-up dose must be measured before admission.
    """
    if not daily:
        return []
    first, last = date.fromisoformat(min(daily)), today-timedelta(days=1)
    candidates = []
    for (kind, key), entry in selected.items():
        p = entry.get("payload") or {}
        if kind != "BLOCK" or p.get("phase") != "BUILD":
            continue
        try:
            start, end = date.fromisoformat(p["start"]), date.fromisoformat(p["load_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if recovery_limit((end-start).days+1) is None or end+timedelta(days=3) > last:
            continue
        load = _mean(daily, start, end)
        follow = _mean(daily, end+timedelta(days=1), end+timedelta(days=3))
        if load and follow is not None and follow <= .8*load and daily.get((end+timedelta(days=1)).isoformat(),float("inf")) <= .8*load:
            candidates.append({"start":start,"load_end":end,"boundary_source":"CONFIRMED_MEASURED_BLOCK"})
    for i in range(max(0, (last-first).days-8)):
        end = first+timedelta(days=i+6)
        if any(c["load_end"] == end for c in candidates):
            continue
        before = _mean(daily, end-timedelta(days=6), end)
        after = _mean(daily, end+timedelta(days=1), end+timedelta(days=3))
        if not before or after is None or after > .8*before or daily.get((end+timedelta(days=1)).isoformat(),float("inf")) > .8*before:
            continue
        # End a block at the first sustained drop, not every day of recovery.
        if any(c["load_end"] <= end <= c["load_end"]+timedelta(days=6) for c in candidates):
            continue
        onsets = []
        for length in range(7,22):
            start = end-timedelta(days=length-1)
            base = _mean(daily, start-timedelta(days=7), start-timedelta(days=1))
            onset = _mean(daily, start, start+timedelta(days=2))
            whole = _mean(daily, start, end)
            previous = _mean(daily, start-timedelta(days=3), start-timedelta(days=1))
            if base and previous and onset is not None and whole is not None and onset >= 1.10*base and onset >= 1.10*previous and whole >= 1.05*base:
                onsets.append((onset/base, start))
        if onsets:
            # Strongest observed transition fixes onset without using outcomes.
            _, start = max(onsets, key=lambda v:(v[0],-v[1].toordinal()))
            candidates.append({"start":start,"load_end":end,"boundary_source":"MEASURED_DOSE_CHANGE"})
    # No two episodes can assign the same loading exposure twice.
    disjoint, through = [], first-timedelta(days=1)
    for block in sorted(candidates, key=lambda c:(c["load_end"], c["start"])):
        if block["start"] <= through:
            continue
        block["end"] = min(last, block["load_end"]+timedelta(days=14))
        following = [c["start"] for c in candidates if c["start"] > block["load_end"]]
        if following:
            block["end"] = min(block["end"], min(following)-timedelta(days=1))
        if block["end"] >= block["load_end"]+timedelta(days=2):
            disjoint.append(block)
            through = block["end"]
    return disjoint


def detect_blocks(daily, selected, today, component_daily=None):
    """A component downshift can identify a block even if total Q barely moves."""
    curves={"TOTAL":daily,**(component_daily or {})}
    candidates=[]
    for component,curve in curves.items():
        relevant={k:e for k,e in selected.items() if k[0]!="BLOCK" or component=="TOTAL" or not e.get("payload",{}).get("components") or component in e["payload"]["components"]}
        for block in _detect_one_curve(curve,relevant,today):
            block["boundary_component"]=component
            candidates.append(block)
    chosen=[]
    for block in sorted(candidates,key=lambda c:(c["load_end"],0 if c["boundary_source"]=="CONFIRMED_MEASURED_BLOCK" else 1,c["start"],c["boundary_component"])):
        if any(block["start"]<=old["end"] and old["start"]<=block["end"] for old in chosen):
            continue
        chosen.append(block)
    return chosen


def _frozen_scores(days, start):
    before = [(start-timedelta(days=n)).isoformat() for n in range(1,29)]
    refs = {}
    for key in ("hrv","resting_hr","sleep_duration"):
        values = [number(c.get("raw")) for d in before for c in days.get(d,{}).get("channels",[]) if c.get("key")==key]
        values = [log(v) if key=="hrv" else v for v in values if v is not None and (key!="hrv" or v>0)]
        if len(values)>=3:
            center=median(values)
            floor=.12 if key=="hrv" else 3. if key=="resting_hr" else .5
            refs[key]={"median":center,"spread":max(floor,1.4826*median(abs(v-center) for v in values)),"count":len(values)}
    weights = [p.get("morning_kg") for d in before if (p:=(days.get(d,{}).get("body_observations",{}).get("weight",{}).get("report") or {})).get("morning_standardized") and number(p.get("morning_kg")) is not None]
    if len(weights)>=3:
        center=median(weights)
        refs["morning_weight"]={"median":center,"spread":max(.2,.005*center,1.4826*median(abs(v-center) for v in weights)),"count":len(weights)}
    def score(row):
        values={}
        for c in row.get("channels",[]):
            key=c.get("key")
            if key not in CORE:
                continue
            if key=="morning_weight":
                p=row.get("body_observations",{}).get("weight",{}).get("report") or {}
                raw=number(p.get("morning_kg")) if p.get("morning_standardized") else None
            else:
                raw=number(c.get("raw"))
            if key in {"hrv","resting_hr","sleep_duration","morning_weight"}:
                ref=refs.get(key)
                if ref is None or raw is None or (key=="hrv" and raw<=0):
                    continue
                val=relative_score(log(raw) if key=="hrv" else raw,ref,1 if key=="resting_hr" else -1)
                values[key]=50+abs(val-50) if key=="hrv" else val
            elif number(c.get("score")) is not None:
                values[key]=c["score"]
        return values
    return score,refs


def _response(days, start, load_end, end):
    scores,refs=_frozen_scores(days,start)
    base_keys=legacy._dates(start-timedelta(days=7),start-timedelta(days=1))
    follow_keys=legacy._dates(load_end+timedelta(days=1),end)
    base={d:scores(days.get(d,{})) for d in base_keys}
    follow={d:scores(days.get(d,{})) for d in follow_keys}
    panel=sorted(k for k in CORE if sum(k in row for row in base.values())>=3 and sum(k in row for row in follow.values())>=2)
    if len(panel)<2:
        return None
    weight=sum(CHANNELS[k][1] for k in panel)
    def total(values):
        return sum(values[k]*CHANNELS[k][1] for k in panel)/weight if all(k in values for k in panel) else None
    before=[v for row in base.values() if (v:=total(row)) is not None]
    if len(before)<3:
        return None
    center=median(before)
    series={d:total(scores(row)) for d,row in days.items()}
    returned=None
    previous=None
    elevated=False
    for day in legacy._dates(start,load_end):
        elevated=elevated or (number(series.get(day)) is not None and series[day]>center+5)
    for day in follow_keys:
        value=series.get(day)
        if value is None or value>center+5:
            previous=None
            if value is not None:
                returned=None
        elif previous is not None and date.fromisoformat(day)-date.fromisoformat(previous)==timedelta(days=1):
            if returned is None:
                returned=previous
            previous=day
        else:
            previous=day
    if not elevated:
        returned=None
    observed=[v for d,v in series.items() if start.isoformat()<=d<=end.isoformat() and number(v) is not None]
    recovery_days=(date.fromisoformat(returned)-load_end).days if returned else None
    return {"burden_delta":round(sum(observed)/len(observed)-center,6) if observed else 0.,"baseline_score":round(center,6),
            "recovery_days":recovery_days,"recovered":returned is not None,"returned_on":returned,
            "coverage":weight/100,"nominal_coverage_percent":weight,"channels":panel,"references":refs,
            "baseline_days":len(before),"followup_days":sum(number(series.get(d)) is not None for d in follow_keys),
            "observed_days":len(observed),"tracked_days":(end-start).days+1,"stress_elevated":elevated,
            "right_censored":elevated and returned is None,"followup_elapsed_days":(end-load_end).days,
            "series":series}


def build_view_data(*, source, rows, history, entries, trainability, today, periodization=None, events=(), retained=None, context_key=""):
    selected=legacy._available_entries(entries,today)
    days=legacy._response_days(history,selected,entries,today)
    rows,excluded_dates=measured_rows(source,[r for r in rows if r.get("date","")<today.isoformat()])
    first=date.fromisoformat(source.get("period_start") or min((r["date"] for r in rows),default=min((d.get("day") for d in history.get("days",[]) if d.get("day")),default=today.isoformat())))
    daily=_daily(source,rows,first,today-timedelta(days=1))
    component_daily={z:_daily(source,rows,first,today-timedelta(days=1),z) for z in COMPONENTS}
    blocks=detect_blocks(daily,selected,today,component_daily)
    episodes,exclusions,view_episodes=[],[],[]
    if unidentified_activity_count(source):
        exclusions.append({"id":None,"reason":"UNKNOWN_EXCLUDED_DATES"})
    previous={e["id"]:e for e in (retained or {}).get("episodes",[]) if e.get("context_key")==context_key and e.get("provenance",{}).get("evidence_version")==VERSION}
    for old in previous.values():
        if old.get("end", "9999")<first.isoformat():
            episodes.append(deepcopy(old))
    graph={d:{"day":d,"actual_q":v,"stress":None,"baseline":None} for d,v in daily.items()}
    for block in blocks:
        start,load_end,end=(block[k] for k in ("start","load_end","end"))
        identifier=f"actual:{start}:{load_end}"
        duration=(load_end-start).days+1
        dose,reason=legacy._dose(source,rows,start,load_end,end)
        response=_response(days,start,load_end,end) if dose else None
        outcomes=[]
        reasons=legacy._confounds(days,selected,history,start-timedelta(days=7),end,periodization,events)
        if response:
            outcomes=legacy._ti_outcomes(trainability,start,response["returned_on"],end)+legacy._test_outcomes(selected,start,response["returned_on"],end)
            for d,v in response.pop("series").items():
                if d in graph and start.isoformat()<=d<=end.isoformat():
                    graph[d].update(stress=v,baseline=response["baseline_score"])
        reason=reason or ("INSUFFICIENT_INDEPENDENT_RESPONSE" if response is None else None)
        if reason:
            exclusions.append({"id":identifier,"start":start.isoformat(),"end":end.isoformat(),"reason":reason})
        response=response or {}
        observed=response.get("recovery_days")
        ratio=observed/recovery_limit(duration) if observed is not None else None
        change=proposed_step(duration,observed) if observed is not None else None
        performance=median(o["change_percent"] for o in outcomes) if outcomes else None
        if outcomes and any(o["change_percent"] < -o.get("meaningful_change_percent",1.) for o in outcomes) and any(o["change_percent"] > o.get("meaningful_change_percent",1.) for o in outcomes):
            reasons.append("CONFLICTING_OUTCOMES")
            outcomes=[]
            performance=None
        status="INSUFFICIENT_DATA" if reason else "CONTEXT_REVIEW" if reasons else "NO_ELEVATED_RESPONSE" if not response.get("stress_elevated") else "RECOVERED" if response.get("recovered") else "DELAYED" if response.get("followup_elapsed_days",0)>recovery_limit(duration) else "OBSERVING"
        description={"INSUFFICIENT_DATA":"Недостатъчни съпоставими измервания.","CONTEXT_REVIEW":"Периодът има различен контекст и не учи общата политика.","NO_ELEVATED_RESPONSE":"Не е наблюдавано повишение на стреса; време за връщане не се измисля.","RECOVERED":"Наблюдавано връщане в два последователни дни.","DELAYED":"Връщането е след експертния срок; наблюдението продължава.","OBSERVING":"Проследяваме независимите показатели."}[status]
        view_episodes.append({"id":identifier,"start":start.isoformat(),"load_end":load_end.isoformat(),"end":end.isoformat(),"loading_days":duration,"recovery_limit_days":round(recovery_limit(duration),4),"recovery_days":observed,"recovered":bool(response.get("recovered")),"ratio":round(ratio,4) if ratio is not None else None,"status":status,"proposed_change_percent":round(change*100,3) if change is not None else 0.,"reason":description,"performance_change_percent":round(performance,4) if performance is not None else None,"boundary_source":block["boundary_source"],"boundary_component":block.get("boundary_component"),"right_censored":response.get("right_censored",False)})
        if reason:
            continue
        provenance={"evidence_version":VERSION,"response_basis":"FROZEN_INDEPENDENT_OBSERVATIONS","dose_basis":"ACTUAL_DIRECT_Q","independent_outcome":bool(outcomes),"confounds":sorted(set(reasons)),"boundary_source":block["boundary_source"]}
        ep={"id":identifier,"start":start.isoformat(),"load_end":load_end.isoformat(),"end":end.isoformat(),"observed_on":today.isoformat(),"context_key":context_key,**dose,"response":response,"outcomes":outcomes,"quality":min(1.,response["observed_days"]/max(1,response["tracked_days"])),"provenance":provenance,"confounded":bool(reasons),"loading_days":duration,"recovery_limit_days":recovery_limit(duration),
            "model_context":{"loading_duration":log(duration/14.),"followup_load":log((1+sum(v["followup_q"] for v in dose["dose"].values()))/(1+sum(v["actual_q"] for v in dose["dose"].values())))}}
        fingerprint=sha256(json.dumps({k:ep[k] for k in ("dose","response","outcomes","confounded")},sort_keys=True).encode()).hexdigest()
        ep["provenance"]["evidence_fingerprint"]=fingerprint
        if identifier in previous and previous[identifier].get("provenance",{}).get("evidence_fingerprint")==fingerprint:
            ep["observed_on"]=previous[identifier]["observed_on"]
        episodes.append(ep)
    # A fixed panel makes disappearance a GAP, never apparent improvement.
    fallback_panel=sorted(k for k in CORE if sum(any(c.get("key")==k and number(c.get("score")) is not None for c in r.get("channels",[])) for r in days.values())>=3)
    fallback_weight=sum(CHANNELS[k][1] for k in fallback_panel)
    for d,row in graph.items():
        if row["stress"] is None:
            vals={c["key"]:c["score"] for c in days.get(d,{}).get("channels",[]) if c.get("key") in fallback_panel and number(c.get("score")) is not None}
            row["stress"]=round(sum(vals[k]*CHANNELS[k][1] for k in fallback_panel)/fallback_weight,4) if fallback_weight and all(k in vals for k in fallback_panel) else None
    if excluded_dates:
        exclusions.append({"id":None,"reason":"INCOMPLETE_MEASURED_DATES","dates":excluded_dates})
    current=legacy._current(days,selected,source,rows,history,today,bool(rows))
    independent_days=[(d,[c for c in r.get("channels",[]) if c.get("key") in CORE and number(c.get("score")) is not None]) for d,r in sorted(days.items())]
    latest=next(((d,cs) for d,cs in reversed(independent_days) if cs),(None,[]))
    d,cs=latest
    nominal=sum(CHANNELS[c["key"]][1] for c in cs)
    expected=set(c["key"] for day,channels in independent_days if d and (date.fromisoformat(d)-timedelta(days=6)).isoformat()<=day<=d for c in channels)
    expected_weight=sum(CHANNELS[k][1] for k in expected)
    current.update(latest_day=d,stress_score=round(sum(c["score"]*CHANNELS[c["key"]][1] for c in cs)/nominal,4) if nominal else None,
                   coverage=100*nominal/expected_weight if expected_weight else 0.,nominal_coverage_percent=nominal,
                   independent_channel_count=len(cs),families=len({CHANNELS[c["key"]][0] for c in cs}),recovered=False)
    if episodes:
        latest_ep=episodes[-1]
        panel=latest_ep["response"]["channels"]
        scorer,_=_frozen_scores(days,date.fromisoformat(latest_ep["start"]))
        pair=legacy._dates(today-timedelta(days=2),today-timedelta(days=1))
        vals=[scorer(days.get(day,{})) for day in pair]
        weight=sum(CHANNELS[k][1] for k in panel)
        current["recovered"]=bool(weight and all(all(k in v for k in panel) and sum(v[k]*CHANNELS[k][1] for k in panel)/weight<=latest_ep["response"]["baseline_score"]+5 for v in vals))
    if current.get("illness_hold") or current.get("lab_review"):
        current["recovered"]=False
    channel_info=[]
    for key in sorted(CORE):
        available=sum(any(c.get("key")==key and number(c.get("score")) is not None for c in row.get("channels",[])) for row in days.values())
        channel_info.append({"key":key,"family":CHANNELS[key][0],"observed_days":available,"used_for":"INDEPENDENT_STRESS"})
    detail={"version":VERSION,"as_of":today.isoformat(),"series":list(graph.values()),"episodes":view_episodes[-24:],
            "decision":{"component":None,"status":"COLLECTING","proposed_change_percent":0.,"applied_change_percent":0.,"reason":"Нужни са съпоставими натоварващи блокове и независими наблюдения."},
            "observations":{"measured_days":sum(v is not None for v in daily.values()),"excluded_days":len(excluded_dates),"unidentified_activities":unidentified_activity_count(source),"accepted_blocks":sum(not e["confounded"] for e in episodes),"reaction_only_blocks":sum(not e["outcomes"] for e in episodes),"performance_blocks":sum(bool(e["outcomes"]) for e in episodes)},"channels":channel_info,
            "settings":{"maximum_change_percent":20.,"loading_days_min":7,"loading_days_max":21,"recovery_days_min":3.,"recovery_days_max":7.,"increase_sensitivity":1.,"decrease_sensitivity":2.},
            "diagnostic":False,"independent_stress":True,"comparison_panel":fallback_panel}
    return {"episodes":episodes[-78:],"current":current,"exclusions":exclusions,"recovery_policy":detail,"_response_days":days,"_recent_window":observed_window(source,rows,today-timedelta(days=7),today)}


def apply_decision(report, evidence, models, *, config, allowed_components, phase, taper, today, retained=None, supported_episodes=None):
    """Translate observed return into one bounded, reversible Q policy decision.

    Bayesian fits continue to learn performance/burden/recovery independently.
    Sparse positive probes remain <=2.5%; validated evidence may gradually use
    the coach's 20% ceiling. A delayed return is a coaching signal, not NFOR.
    """
    detail=evidence["recovery_policy"]
    current=evidence["current"]
    mode=config.get("mode","SHADOW")
    maximum=min(.20,max(0.,config.get("max_volume_step_percent",20.)/100))
    detail["settings"]["maximum_change_percent"]=100*maximum
    neutral=individual_learning._neutral_components("Няма допусната индивидуална промяна.")
    report.update(components=neutral,predictions={},decision=None,confidence="LOW")
    report["recovery_policy"]=detail
    decision=detail["decision"]
    def hold(reason,status="COLLECTING"):
        report.update(status=status,summary=reason)
        decision.update(status=status,reason=reason)
        return report
    if mode=="OFF":
        return hold("Самообучението е изключено.","OFF")
    latest=current.get("latest_day")
    fresh=bool(latest and 0 <= (today-date.fromisoformat(latest)).days <= 2)
    if current.get("illness_hold") or current.get("lab_review") or current.get("execution_caution"):
        return hold("Има наблюдение за преглед на състоянието; индивидуална промяна е задържана.","CAUTION")
    if not fresh or current.get("independent_channel_count",0)<2 or current.get("coverage",0)<80 or not current.get("history_usable"):
        return hold("Нужни са актуални независими показатели и пълни измерени дни на натоварване.")
    if phase not in individual_learning.PREPARATION or taper or not allowed_components:
        return hold("Текущият период или ръчните цели не допускат нова промяна.","SHADOW" if mode=="SHADOW" else "READY")
    usable=[e for e in (evidence["episodes"] if supported_episodes is None else supported_episodes) if not e.get("confounded") and (today-date.fromisoformat(e["load_end"])).days <= 42]
    if not usable:
        return hold("Нужен е съпоставим реален блок с независимо проследена реакция.")
    episode=max(usable,key=lambda e:(e["load_end"],e["start"]))
    response=episode["response"]
    # Current recovery must use THIS supported episode's frozen panel.
    panel=response.get("channels",[])
    scorer,_=_frozen_scores(evidence.get("_response_days",{}),date.fromisoformat(episode["start"]))
    pair=legacy._dates(date.fromisoformat(latest)-timedelta(days=1),date.fromisoformat(latest))
    values=[scorer(evidence.get("_response_days",{}).get(d,{})) for d in pair]
    weight=sum(CHANNELS[k][1] for k in panel)
    current["recovered"]=bool(weight and all(all(k in v for k in panel) and sum(v[k]*CHANNELS[k][1] for k in panel)/weight<=response["baseline_score"]+5 for v in values))
    if not response.get("stress_elevated"):
        return hold("Не е наблюдавано повишение на стреса; няма измерено време за връщане.")
    previous=(retained or {}).get("decision") or {}
    if previous.get("source_episode_id") and previous.get("expires_on", "") >= today.isoformat() and (previous.get("mode") != mode or previous.get("config") != config):
        return hold("Променените настройки изчакват края на започнатото наблюдение.","SHADOW" if mode=="SHADOW" else "READY")
    if previous.get("source_episode_id")==episode["id"] and previous.get("expires_on", "") < today.isoformat():
        return hold("Приключи наблюдението на предходната промяна; изчакваме нов изпълнен блок.","SHADOW" if mode=="SHADOW" else "READY")
    if previous.get("source_episode_id") and previous.get("source_episode_id")!=episode["id"] and previous.get("expires_on", "")>=today.isoformat():
        return hold("Изчакваме края на наблюдението на вече започнатата промяна.","SHADOW" if mode=="SHADOW" else "READY")
    observed=response.get("recovery_days")
    limit=episode["recovery_limit_days"]
    censored=observed is None
    elapsed=response.get("followup_elapsed_days",0)
    if censored and (not response.get("right_censored") or elapsed<=limit or response.get("followup_days",0)<max(2,elapsed-1)):
        return hold("Проследяването продължава; липсва потвърдено време за връщане.")
    days=elapsed if censored else observed
    ratio=days/limit
    eligible=[z for z in COMPONENTS if z in allowed_components and number((episode.get("dose",{}).get(z) or {}).get("actual_q")) is not None and episode["dose"][z]["actual_q"]>0]
    if not eligible:
        return hold("За текущите компоненти няма положителна измерена доза за сравнение.")
    previous_component=previous.get("component") if previous.get("source_episode_id")==episode["id"] else None
    component=previous_component if previous_component in eligible else max(eligible,key=lambda z:(episode["dose"][z]["actual_q"]/(episode["dose"][z]["baseline_q"]+1),-COMPONENTS.index(z)))
    execution_ceiling=None
    if not current.get("recovered"):
        recent=evidence.get("_recent_window") or {}
        execution_ceiling=(recent.get("components",{}).get(component) or {}).get("weekly_q")
        if not recent.get("complete") or number(execution_ceiling) is None or execution_ceiling<0:
            return hold("Текущото връщане не е потвърдено и няма пълна измерена база за продължаване на разтоварването.")
    relevant=[o for o in episode.get("outcomes",[]) if o.get("scope") in ("GLOBAL",component)]
    model=models.get(component) or models["GLOBAL"]
    if model["validation"]["status"]!="PASSED":
        model=models["GLOBAL"]
    perf_count=model["posterior"]["count"]
    validated=(model["validation"]["status"]=="PASSED" and perf_count>=8 and
               individual_learning._identified(model["samples"],individual_learning.FEATURES.index(f"volume:{component}")+1)>=2)
    positive_episodes=[e for e in usable if e["response"].get("recovered") and any(o.get("scope") in ("GLOBAL",component) for o in e.get("outcomes",[]))]
    if ratio<1:
        if not current.get("recovered") or (number(current.get("stress_score")) is not None and current["stress_score"]>=75) or not relevant or any(o["change_percent"] < -o.get("meaningful_change_percent",1.) for o in relevant):
            return hold("Увеличение изисква потвърдено възстановяване и запазена съпоставима работоспособност.")
        if validated:
            confidence=min(1.,perf_count/16.)
        elif config.get("exploration_enabled",True) and len(positive_episodes)>=2 and models["GLOBAL"]["validation"]["status"]!="FAILED":
            confidence=.125
        else:
            return hold("Натрупваме независими резултати преди първата малка положителна проба.")
        step=min(maximum,proposed_step(episode["loading_days"],days,confidence))
        if validated:
            contrast=individual_learning.action_contrast(component,"volume",step,{z:{"weekly_q":v["actual_q"],"weekly_minutes":v["actual_minutes"]} for z,v in episode["dose"].items()})
            gain=individual_learning.predict(model["posterior"],contrast,observation=False) if contrast else None
            burden=individual_learning.predict(models["burden"]["posterior"],contrast,observation=False) if contrast else None
            recovery_change=individual_learning.predict(models["recovery"]["posterior"],contrast,observation=False) if contrast else None
            if not gain or gain["low"]<=0 or not burden or burden["high"]>10 or not recovery_change or days+max(0.,recovery_change["high"])>limit:
                return hold("Неопределеността или прогнозираното допълнително възстановяване задържат положителната промяна.")
    else:
        confidence=1.
        step=max(-maximum,proposed_step(episode["loading_days"],days))
    if previous_component==component and previous.get("expires_on", "")>=today.isoformat():
        old_step=previous.get("step")
        if number(old_step) is not None and old_step>0 and step>0:
            step=min(step,old_step)
    effective=previous.get("effective_from") if previous_component==component and previous.get("expires_on", "")>=today.isoformat() else today.isoformat()
    expires=(date.fromisoformat(effective)+timedelta(days=episode["loading_days"]-1)).isoformat()
    baseline=previous.get("baseline_actual_weekly_q") if previous_component==component and previous.get("expires_on", "")>=today.isoformat() else episode["dose"][component]["actual_q"]
    reason=("Реалната съпоставима базова цел се запазва при връщане на експертната граница." if step==0 else "Потвърденото връщане и независимият резултат допускат ограничена положителна проба." if step>0 else "Връщането е забавено спрямо експертния срок; по-ниска базова цел за следващия съпоставим блок.")
    if execution_ceiling is not None:
        reason+=" Текущото разтоварване се ограничава до измерената последна седмица до наблюдавано връщане."
    c=neutral[component]
    c.update(proposed_volume_factor=round(1+step,6),volume_factor=round(1+step,6) if mode=="CONTROL" else 1.,confidence="MEDIUM" if validated else "LOW",reason=reason,action="INCREASE" if step>0 else "DECREASE" if step<0 else "HOLD",evidence_count=perf_count,baseline_actual_weekly_q=baseline,execution_weekly_q_ceiling=execution_ceiling)
    choice={"component":component,"kind":"volume","step":step,"source_episode_id":episode["id"],"baseline_actual_weekly_q":baseline,"mode":mode,"config":deepcopy(config),"effective_from":effective,"expires_on":expires,"experimental":not validated,"validation":model["validation"],"ratio":ratio,"right_censored":censored,"execution_weekly_q_ceiling":execution_ceiling}
    report.update(status="SHADOW" if mode=="SHADOW" else "READY",summary=f"{component}: {step*100:+.1f}% спрямо реалния съпоставим блок. "+reason,decision=choice,effective_from=effective,expires_on=expires,confidence=c["confidence"],validation=model["validation"])
    report["decision_id"]=sha256(json.dumps(choice,sort_keys=True,default=str).encode()).hexdigest()[:16]
    decision.update(component=component,status=report["status"],proposed_change_percent=round(step*100,3),applied_change_percent=round(step*100,3) if mode=="CONTROL" else 0.,reason=reason,baseline_actual_weekly_q=baseline,target_weekly_q=round(baseline*(1+step),4),source_episode_id=episode["id"],confidence=c["confidence"],right_censored=censored,execution_weekly_q_ceiling=execution_ceiling)
    return report
