"""Individual speed-duration models over a fixed expert-reference domain.

The canonical Curve and normative interpolation remain unchanged. One real
anchor scales that normative shape; two or more anchors select an exact
individual window with bounded five-percent continuation. No mode extends beyond the
reference duration domain or turns model-generated points into observations.
"""
from __future__ import annotations
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
import math

VERSION = "speed-duration-individual-5pct-v4"
NORMATIVE_VERSION = "speed-duration-c1-duration-volume-v2"
REFERENCE_TIMES = (10.8, 60., 180., 1200., 7200., 43516.)
REFERENCE_SPEEDS = (9.405516961260822, 8.543876534348628, 7.346875281685026,
                    6.271989154390681, 5.698632863726023, 3.7974582869384923)
VOLUME_RANGES_MIN = {"Z1":(240.,840.), "Z2":(60.,300.), "Z3":(30.,120.),
                     "Z4":(10.,40.), "Z5":(5.,30.)}


def positive(v):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v <= 0:
        raise ValueError("A positive finite measurement is required")
    return float(v)


@dataclass(frozen=True)
class Curve:
    times: tuple[float,...]
    speeds: tuple[float,...]

    def __post_init__(self):
        if len(self.times)!=len(self.speeds) or len(self.times)<2:
            raise ValueError("At least two matching knots required")
        x,y=tuple(math.log(positive(t)) for t in self.times),tuple(math.log(positive(v)) for v in self.speeds)
        if any(a>=b for a,b in zip(x,x[1:])):
            raise ValueError("Test durations must be different")
        slopes=[(b-a)/(d-c) for a,b,c,d in zip(y,y[1:],x,x[1:])]
        if any(not -1 < s < 0 for s in slopes):
            raise ValueError("Tests conflict: longer efforts require lower speed and greater distance")
        tangents=[slopes[0]]+[2*a*b/(a+b) for a,b in zip(slopes,slopes[1:])]+[slopes[-1]]
        pieces=[]
        for i,s in enumerate(slopes):
            eta=.2
            for _ in range(60):
                plateau=(s-eta*(tangents[i]+tangents[i+1])/2)/(1-eta)
                if -1 < plateau < 0:
                    break
                eta/=2
            else:
                raise ValueError("Measurements are too close to a non-invertible curve")
            pieces.append((eta,plateau,tangents[i],tangents[i+1]))
        object.__setattr__(self,"_x",x)
        object.__setattr__(self,"_y",y)
        object.__setattr__(self,"_pieces",pieces)

    def speed(self,t):
        t=positive(t)
        if not self.times[0]*(1-1e-12)<=t<=self.times[-1]*(1+1e-12):
            raise ValueError("Outside the calibrated duration range")
        x=math.log(min(self.times[-1],max(self.times[0],t)))
        i=min(len(self.times)-2,max(0,bisect_right(self._x,x)-1))
        h=self._x[i+1]-self._x[i]
        u=(x-self._x[i])/h
        e,b,l,r=self._pieces[i]
        if u<e:
            integral=l*u+(b-l)*u*u/(2*e)
        elif u<=1-e:
            integral=e*(l+b)/2+(u-e)*b
        else:
            w=u-(1-e)
            integral=e*(l+b)/2+(1-2*e)*b+b*w+(r-b)*w*w/(2*e)
        return math.exp(self._y[i]+h*integral)

    def distance(self,t):
        return t*self.speed(t)

    def log_slope(self,t):
        self.speed(t)  # validate the domain
        x=math.log(t)
        i=min(len(self.times)-2,max(0,bisect_right(self._x,x)-1))
        u=(x-self._x[i])/(self._x[i+1]-self._x[i])
        e,b,l,r=self._pieces[i]
        return l+(b-l)*u/e if u<e else b if u<=1-e else b+(r-b)*(u-1+e)/e

    def inverse(self,value,*,distance=False):
        value=positive(value)
        fn=self.distance if distance else self.speed
        bounds=sorted((fn(self.times[0]),fn(self.times[-1])))
        if not bounds[0]*(1-1e-10)<=value<=bounds[1]*(1+1e-10):
            raise ValueError("Outside the calibrated prediction range")
        lo,hi=self._x[0],self._x[-1]
        for _ in range(65):
            mid=(lo+hi)/2
            if (fn(math.exp(mid))<value)==distance: lo=mid
            else: hi=mid
        return math.exp((lo+hi)/2)


class ScaledCurve:
    """Scale the canonical normative shape through exactly one measured anchor."""
    calibration_mode = "SINGLE_ANCHOR_SCALE"
    model_version = VERSION

    def __init__(self, prior, duration_s, speed_mps):
        self.prior = prior
        self.ratio = positive(speed_mps)/prior.speed(duration_s)
        self.times, self._x = prior.times, prior._x
        self.speeds = tuple(self.speed(t) for t in self.times)
        self.anchor = duration_s

    def speed(self, t): return self.prior.speed(t)*self.ratio
    def distance(self, t): return t*self.speed(t)
    def log_slope(self, t): return self.prior.log_slope(t)
    def inverse(self, value, *, distance=False): return Curve.inverse(self, value, distance=distance)
    def point_metadata(self, t):
        self.speed(t)
        return {"calibration_mode": self.calibration_mode, "extrapolated": t != self.anchor,
            "within_test_window": t == self.anchor, "additional_correction": 0., "extrapolation_capped": False}


@lru_cache(maxsize=16)
def _two_anchor_curve(pairs):
    from .speed_duration_5pct import TwoAnchorCurve
    return TwoAnchorCurve(Curve(REFERENCE_TIMES, REFERENCE_SPEEDS), pairs)


@lru_cache(maxsize=16)
def _multipoint_curve(pairs):
    from .speed_duration_5pct import MultipointCurve
    return MultipointCurve(Curve(REFERENCE_TIMES, REFERENCE_SPEEDS), pairs)


def model_metadata(curve):
    mode = getattr(curve, "calibration_mode", "REFERENCE_ONLY")
    return {"calibration_mode": mode,
        "model_version": getattr(curve, "model_version", NORMATIVE_VERSION),
        "normative_version": NORMATIVE_VERSION,
        "additional_corridor_fraction": .05 if mode in ("TWO_ANCHOR_5PCT", "MULTIPOINT_C1_5PCT") else None}


def calibrated(tests, *, prior=None):
    reference=Curve(REFERENCE_TIMES,REFERENCE_SPEEDS)
    pairs=tuple(sorted((positive(t["duration_s"]),positive(t["speed_kmh"])/3.6) for t in tests))
    if not pairs:
        return prior if prior is not None else reference
    if len(pairs)==1:
        t,v=pairs[0]
        return ScaledCurve(reference, t, v)
    for t,v in pairs:
        reference.speed(t)  # domain check
    if any(t1 >= t2 or not -1 < math.log(v2/v1)/math.log(t2/t1) < 0
           for (t1,v1),(t2,v2) in zip(pairs,pairs[1:])):
        raise ValueError("Tests conflict: longer efforts require lower speed and greater distance")
    if len(pairs)==2:
        # The checked two-anchor specification always uses canonical N, never
        # a silently substituted HR-derived prior or model-generated test.
        return _two_anchor_curve(pairs)
    return _multipoint_curve(pairs)


def volume_correction(zone, weekly_min):
    if weekly_min is None:
        return 0.
    if not math.isfinite(weekly_min) or weekly_min<0:
        raise ValueError("Invalid volume history")
    lo,hi=VOLUME_RANGES_MIN[zone]
    return max(-.1,min(.1,.2*(weekly_min-lo)/(hi-lo)-.1))


def smoothstep(x):
    x=max(0.,min(1.,x))
    return x*x*(3-2*x)


def zone_adjustment(hr, centers, corrections):
    if hr<=centers[0]: return corrections[0]
    if hr>=centers[-1]: return corrections[-1]
    i=bisect_right(centers,hr)-1
    w=smoothstep((hr-centers[i])/(centers[i+1]-centers[i]))
    return corrections[i]*(1-w)+corrections[i+1]*w


def adjusted(curve, tests, hr_for_speed, centers, corrections, *, duration_centers=None):
    """Warp duration, fade the correction to zero at observed anchors.

    Build the resulting C1 bounded curve, reducing correction if necessary.
    This validates speed and distance monotonicity mathematically per segment,
    rather than trusting a chart grid to establish physical invertibility.
    """
    if getattr(curve,"calibration_mode",None) in ("SINGLE_ANCHOR_SCALE", "TWO_ANCHOR_5PCT", "MULTIPOINT_C1_5PCT"):
        # History positions preliminary capacities. Later time warping must
        # not change a calibrated shape or its relative continuation corridor.
        return curve,0.
    if (not hr_for_speed and duration_centers is None) or not tests or not any(corrections):
        return curve,0.
    locations = [-math.log(c) for c in duration_centers] if duration_centers is not None else centers
    anchors=[math.log(t["duration_s"]) for t in tests]
    x0,x1=curve._x[0],curve._x[-1]
    fixed=sorted(set([t["duration_s"] for t in tests]+list(curve.times)))
    grid=[math.exp(x0+(x1-x0)*i/180) for i in range(1,180)]
    times=sorted(fixed+[t for t in grid if all(abs(math.log(t/f))>1e-8 for f in fixed)])
    for factor in (1.,.5,.25,.125,.0625,0.):
        pairs=[]
        for t in times:
            v=curve.speed(t)
            fade=math.prod(smoothstep(abs(math.log(t)-a)/.35) for a in anchors)
            # Keep the reference domain endpoints fixed as well.
            fade*=smoothstep((math.log(t)-x0)/.2)*smoothstep((x1-math.log(t))/.2)
            position = -math.log(t) if duration_centers is not None else hr_for_speed(v*3.6)
            correction=zone_adjustment(position,locations,corrections)*fade*factor
            pairs.append((t*(1+correction),v))
        try:
            return Curve(tuple(t for t,v in pairs),tuple(v for t,v in pairs)),factor
        except ValueError:
            continue
    return curve,0.


def critical_speed(tests):
    # Explicit eligibility; do not fit from reference/model-generated points.
    chosen=sorted((t["duration_s"],t["duration_s"]*t["speed_kmh"]/3.6)
                  for t in tests if t.get("use_for_cs") and t.get("test_mode","STRICT")=="STRICT" and 120<=t["duration_s"]<=1200)
    if len(chosen)<2:
        return {"status":"INSUFFICIENT_TESTS","count":len(chosen)}
    if chosen[-1][0]/chosen[0][0]<2:
        return {"status":"INSUFFICIENT_DURATION_SPREAD","count":len(chosen)}
    n=len(chosen); mt=sum(t for t,s in chosen)/n; ms=sum(s for t,s in chosen)/n
    denom=sum((t-mt)**2 for t,s in chosen)
    cs=sum((t-mt)*(s-ms) for t,s in chosen)/denom
    d=ms-cs*mt
    if cs<=0 or d<=0 or any(cs>=s/t for t,s in chosen):
        return {"status":"INCONSISTENT_TESTS","count":n}
    rmse=math.sqrt(sum((s-cs*t-d)**2 for t,s in chosen)/n)
    return {"status":"PRELIMINARY_TWO_TESTS" if n==2 else "FITTED", "count":n,
        "speed_kmh":cs*3.6,"d_prime_m":d,"distance_rmse_m":rmse,
        "max_speed_residual_kmh":max(abs(s-cs*t-d)/t*3.6 for t,s in chosen)}


def standardized_critical_speed(tests, curve=None):
    """A derived 3/12-minute descriptor, never two new measured anchors.

    The admitted real short/long tests establish support. Every accepted real
    anchor remains in the calibrated curve, including intermediate tests.
    The 5% continuation corridor is not a confidence interval for CS.
    """
    anchors = [t for t in tests if t.get("maximal")
                and t.get("comparable", True) and t.get("enabled", True)
                and t.get("test_mode", "STRICT") == "STRICT"
                and str(t.get("source", "")).upper() not in {"MODEL", "MODEL_GENERATED", "GENERATED", "ESTIMATED", "REFERENCE"}
                and not t.get("generated") and not t.get("is_estimated")
                and t.get("is_maximal_test") is not False]
    accepted = [t for t in anchors if t.get("use_for_cs")]
    short = [t for t in accepted if 120 <= t["duration_s"] <= 300]
    long = [t for t in accepted if 480 <= t["duration_s"] <= 1200]
    result = {"status": "INSUFFICIENT_SHORT_LONG_TESTS", "version": "cs-standardized-3-12-v1",
              "source": "DERIVED_FROM_INDIVIDUAL_CURVE", "measured": False,
              "count": len(accepted), "points": [], "uncertainty": "NOT_QUANTIFIED"}
    if not short or not long:
        return result
    try:
        curve = curve or calibrated(anchors)
        first, last = min(t["duration_s"] for t in anchors), max(t["duration_s"] for t in anchors)
        points = []
        for seconds in (180., 720.):
            speed = curve.speed(seconds)*3.6
            exact = any(math.isclose(t["duration_s"], seconds) for t in anchors)
            meta = curve.point_metadata(seconds) if hasattr(curve, "point_metadata") else {}
            points.append({"duration_s": seconds, "speed_kmh": speed,
                           "distance_m": seconds*speed/3.6,
                           "evidence": "MEASURED" if exact else "INTERPOLATED" if first <= seconds <= last else "EXTRAPOLATED",
                           "capped": bool(meta.get("extrapolation_capped"))})
        cs = (points[1]["distance_m"]-points[0]["distance_m"])/540
        reserve = points[0]["distance_m"]-cs*180
        if cs <= 0 or reserve <= 0 or cs*3.6 >= points[1]["speed_kmh"]:
            raise ValueError("Inconsistent standardized points")
    except (ValueError, OverflowError):
        return {**result, "status": "INCONSISTENT_TESTS"}
    direct = critical_speed(accepted)
    original_cs = direct.get("speed_kmh")
    return {**result, "status": "MODEL_ESTIMATE", "speed_kmh": cs*3.6,
            "d_prime_m": reserve, "points": points,
            "direct_test_speed_kmh": original_cs,
            "difference_percent": 100*(cs*3.6/original_cs-1) if original_cs else None,
            "real_test_window_s": [first, last],
            "uses_extrapolation": any(p["evidence"] == "EXTRAPOLATED" for p in points),
            "short_test_duration_s": min(short, key=lambda t: abs(t["duration_s"]-180))["duration_s"],
            "long_test_duration_s": min(long, key=lambda t: abs(t["duration_s"]-720))["duration_s"]}
