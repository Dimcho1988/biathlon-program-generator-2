"""One invertible HR-duration map, composed with the calibrated Vflat curve.

Bounds are expert Tmax limits at Z1-Z4 upper HR edges. Z5 shares Z4's
edge; no independent Z5 time limit is invented. Expert fallback durations are
chosen once at construction, never independently for each query direction.
Callers without history retain the legacy midpoint fallback.
"""
from bisect import bisect_right
import math
from .equivalence import DEFAULT_EQUIVALENCE_SLOPE_PP_PER_BPM, Z5_EQUIVALENCE_SLOPE_PP_PER_BPM

VERSION='hr-speed-expert-time-paired-v5'
# Expert continuous capacity at each upper HR edge, not weekly Q or session dose.
# Revised coaching ranges, 2026-09-30: 210–300 / 150–180 / 45–60 / 20–30 min.
TMAX_RANGES_S={'Z1':(12600.,18000.),'Z2':(9000.,10800.),'Z3':(2700.,3600.),'Z4':(1200.,1800.)}
SLOPE=DEFAULT_EQUIVALENCE_SLOPE_PP_PER_BPM/100
Z5_SLOPE=Z5_EQUIVALENCE_SLOPE_PP_PER_BPM/100


def volume_duration_centers(bounds):
    # Locate volume corrections at the agreed Tmax midpoints. Do not apply the
    # HR equivalence to wide Z1 here: that could move its center beyond the curve.
    centers=[sum(limits)/2 for limits in TMAX_RANGES_S.values()]
    centers.append(centers[-1]/(1+Z5_SLOPE*(bounds[5]-bounds[4])/2))
    return centers


class Predictor:
    def __init__(self,curve,bounds,hrmax,indices,*,expert_durations=None,normalization_offset_bpm=0.):
        from .sport_heart_rate import validate_offset
        self.normalization_offset_bpm=validate_offset(normalization_offset_bpm)
        self.curve=curve;self.bounds=tuple(float(x) for x in bounds);self.hrmax=float(hrmax)
        if len(self.bounds)!=6 or not all(math.isfinite(x) for x in self.bounds) or not math.isfinite(self.hrmax) or any(a>=b for a,b in zip(self.bounds,self.bounds[1:])) or self.hrmax<=0:
            raise ValueError('Invalid HR profile')
        fallback={z:sum(limits)/2 for z,limits in TMAX_RANGES_S.items()}
        self.expert_duration_conflicts=[]
        if expert_durations is not None:
            fallback={z:expert_durations.get(z,limits[0]) for z,limits in TMAX_RANGES_S.items()}
            if any(isinstance(t,bool) or not isinstance(t,(int,float)) or not math.isfinite(t)
                   or not TMAX_RANGES_S[z][0]<=t<=TMAX_RANGES_S[z][1] for z,t in fallback.items()):
                raise ValueError('Expert durations must lie within the zone time bounds')
            self.expert_duration_conflicts=[[a,b] for a,b in zip(fallback,list(fallback)[1:]) if fallback[a]<=fallback[b]]
            if self.expert_duration_conflicts:
                fallback={z:limits[0] for z,limits in TMAX_RANGES_S.items()}
        def fallback_source(zone):
            if expert_durations is None:return 'EXPERT_MIDPOINT'
            return 'EXPERT_MINIMUM' if fallback[zone]==TMAX_RANGES_S[zone][0] else 'EXPERT_HISTORY'
        self.anchors=[]
        for i,(zone,limits) in enumerate(TMAX_RANGES_S.items()):
            estimate=indices.get(zone,{}).get('index');candidate=None;speed=None
            candidate_reason='NO_VALID_INDEX'
            if estimate is not None and math.isfinite(estimate) and estimate>0:
                speed=(100*(self.bounds[i+1]+self.normalization_offset_bpm)/(self.hrmax+self.normalization_offset_bpm))/estimate
                try:candidate=curve.inverse(speed/3.6)
                except ValueError:pass
                # The inverse is numerical: an exact boundary can return e.g.
                # 7199.999999999996. Preserve paired evidence at the boundary;
                # this tolerance is rounding only, not relaxed expert limits.
                if candidate is not None:
                    for edge in limits:
                        if math.isclose(candidate,edge,rel_tol=1e-10,abs_tol=1e-8):
                            candidate=edge
                            break
                if candidate is None:
                    candidate_reason='SPEED_BELOW_CURVE' if speed<curve.speed(curve.times[-1])*3.6 else 'SPEED_ABOVE_CURVE'
                elif candidate<limits[0]:candidate_reason='DURATION_BELOW_MIN'
                elif candidate>limits[1]:candidate_reason='DURATION_ABOVE_MAX'
                else:candidate_reason='ACCEPTED'
            accepted=candidate is not None and limits[0]<=candidate<=limits[1]
            duration=candidate if accepted else fallback[zone]
            self.anchors.append({'zone':zone,'hr_bpm':self.bounds[i+1],'duration_s':duration,
                                 'duration_min_s':limits[0],'duration_max_s':limits[1],
                                 'candidate_duration_s':candidate,'index':estimate,'count':indices.get(zone,{}).get('count',0),
                                 'candidate_speed_kmh':speed,'candidate_reason':candidate_reason,
                                 'source':'INDEX' if accepted else fallback_source(zone),
                                 'reason':None if accepted else 'CONFLICTING_EXPERT_DURATION_ESTIMATES' if self.expert_duration_conflicts else 'NO_VALID_INDEX' if estimate is None else 'INDEX_OUTSIDE_DURATION_BOUNDS'})
        # Overlapping ranges cannot guarantee monotonic anchors independently.
        # Retain the supplied ordered expert estimates, or the legacy midpoints.
        # Neither path changes the calibrated speed curve or its real anchors.
        self.conflicting_zones=[[a['zone'],b['zone']] for a,b in zip(self.anchors,self.anchors[1:]) if a['duration_s']<=b['duration_s']]
        if self.conflicting_zones:
            for a in self.anchors:
                a['duration_s']=fallback[a['zone']]
                a['source']=fallback_source(a['zone']);a['reason']='CONFLICTING_ZONE_ANCHORS'
        for a in self.anchors:a['speed_kmh']=curve.speed(a['duration_s'])*3.6
        self.times=tuple(a['duration_s'] for a in self.anchors)
        # Match the existing equivalent-time coefficient within each zone.
        # A short boundary bridge removes jumps between independent zone anchors.
        self.joins={}
        for i in range(1,4):
            lo,hi=self.bounds[i:i+2];previous,current=self.times[i-1:i+1]
            required=hi-(1-current/previous)/SLOPE
            self.joins[i]=min(hi,(max(lo+min(1.,(hi-lo)/4),required+.15*(hi-required))))
        max_t=curve.times[-1]
        self.min_hr=max(self.bounds[0],self.bounds[1]-(1-self.times[0]/max_t)/SLOPE)
        self.max_hr=min(self.bounds[-1],self.hrmax)
        if self.max_hr<=self.min_hr:raise ValueError('Empty HR prediction domain')
        self.max_duration=self.duration(self.min_hr);self.min_duration=self.duration(self.max_hr)
        self.speed_range=(curve.speed(self.max_duration)*3.6,curve.speed(self.min_duration)*3.6)

    def zone_index(self,hr):
        return min(4,max(0,bisect_right(self.bounds,hr)-1))

    def duration(self,hr):
        if not isinstance(hr,(int,float)) or isinstance(hr,bool) or not math.isfinite(hr) or not self.min_hr-1e-9<=hr<=self.max_hr+1e-9:
            raise ValueError('Outside HR prediction range')
        i=self.zone_index(hr)
        if i==4:return self.times[3]/(1+Z5_SLOPE*(hr-self.bounds[4]))
        upper=self.bounds[i+1];k=1-SLOPE*(upper-hr)
        if i and hr<self.joins[i]:
            lower=self.bounds[i];join=self.joins[i]
            target=self.times[i]/(1-SLOPE*(upper-join))
            w=(hr-lower)/(join-lower)
            return math.exp((1-w)*math.log(self.times[i-1])+w*math.log(target))
        if k<=0:raise ValueError('Outside equivalent-time domain')
        return self.times[i]/k

    def hr_for_duration(self,t):
        if not math.isfinite(t) or not self.min_duration*(1-1e-10)<=t<=self.max_duration*(1+1e-10):
            raise ValueError('Outside HR-duration range')
        lo,hi=self.min_hr,self.max_hr
        for _ in range(60):
            mid=(lo+hi)/2
            if self.duration(mid)>t:lo=mid
            else:hi=mid
        return (lo+hi)/2

    def speed_for_hr(self,hr):return self.curve.speed(self.duration(hr))*3.6
    def hr_for_speed(self,v):return self.hr_for_duration(self.curve.inverse(v/3.6))
    def metadata(self,hr):
        i=self.zone_index(hr);anchor=self.anchors[min(3,i)]
        if 0<i<4 and hr<self.joins[i]:
            previous=self.anchors[i-1]
            if hr==self.bounds[i] or previous['source'].startswith('EXPERT_'):anchor=previous
        return {'hr_prediction_source':anchor['source'],'hr_prediction_reason':anchor['reason'],'zone':f'Z{i+1}'}
    def summary(self):
        return {'model_version':VERSION,'hr_range_bpm':[self.min_hr,self.max_hr],
                'hr_reference_offset_bpm':self.normalization_offset_bpm,
                'curve_duration_range_s':[self.curve.times[0],self.curve.times[-1]],
                'curve_speed_range_kmh':[self.curve.speed(self.curve.times[-1])*3.6,self.curve.speed(self.curve.times[0])*3.6],
                'conflicting_zones':self.conflicting_zones,'expert_duration_conflicts':self.expert_duration_conflicts,
                'speed_range_kmh':list(self.speed_range),'equivalence_slope_percent_per_bpm':SLOPE*100,
                'z5_equivalence_slope_percent_per_bpm':Z5_SLOPE*100,
                'zones':self.anchors+[{'zone':'Z5','hr_bpm':self.bounds[4],'duration_s':self.times[3],
                                     'duration_min_s':TMAX_RANGES_S['Z4'][0],'duration_max_s':TMAX_RANGES_S['Z4'][1],
                                     'speed_kmh':self.curve.speed(self.times[3])*3.6,'source':'Z4_SHARED_BOUNDARY',
                                     'index':None,'count':0,'reason':None,'candidate_duration_s':None,
                                     'candidate_speed_kmh':None,'candidate_reason':'Z4_SHARED_BOUNDARY'}]}


class ZonalPredictor:
    """Invert one common accounting profile without moving measured TI edges.

    Expert duration bounds are diagnostic only. The selected continuous V(t)
    is inverted at the accepted boundary, never used to replace that boundary.
    Z5 interpolation is an accounting estimate, not a sprint-HR observation.
    """
    def __init__(self, curve, bounds, hrmax, indices, *, expert_durations=None,
                 normalization_offset_bpm=0., sport="Run", tests=(), profile=None):
        from copy import deepcopy
        from . import zone_profile
        from .speed_duration import with_ten_second_tail
        curve = with_ten_second_tail(curve)
        self.curve = curve
        self.bounds = tuple(float(v) for v in bounds)
        self.hrmax = float(hrmax)
        self.normalization_offset_bpm = float(normalization_offset_bpm)
        self.profile = deepcopy(profile) if profile and profile.get("version") == zone_profile.VERSION and profile.get("status") == "AVAILABLE" else zone_profile.build(curve,
            tuple(v+self.normalization_offset_bpm for v in self.bounds),
            self.hrmax+self.normalization_offset_bpm, indices, sport=sport, tests=tests)
        if self.profile["status"] != "AVAILABLE":
            raise ValueError(self.profile.get("reason") or "Common profile unavailable")
        self.min_hr, self.max_hr = self.bounds[0], self.bounds[-1]
        self.speed_range = (self.profile["bounds_kmh"][0], self.profile["bounds_kmh"][-1])
        self.anchors = []
        for i, zone in enumerate(("Z1", "Z2", "Z3", "Z4")):
            speed = self.profile["bounds_kmh"][i+1]
            try:
                duration = curve.inverse(speed/3.6)
            except (ValueError, ArithmeticError):
                duration = None
            source = self.profile["zones"][i]["boundary_source"]
            self.anchors.append({"zone": zone, "hr_bpm": self.bounds[i+1],
                "duration_s": duration, "speed_kmh": speed,
                "duration_min_s": TMAX_RANGES_S[zone][0], "duration_max_s": TMAX_RANGES_S[zone][1],
                "candidate_duration_s": duration, "candidate_speed_kmh": speed,
                "candidate_reason": "BOUNDARY_PRESERVED", "source": "INDEX" if "INDEX" in source else source,
                "boundary_source": source, "index": (indices.get(zone) or {}).get("index"),
                "count": (indices.get(zone) or {}).get("count", 0), "reason": None})
        self.times = tuple(a["duration_s"] for a in self.anchors)

    def zone_index(self, hr):
        return min(4, max(0, bisect_right(self.bounds, hr)-1))

    def speed_for_hr(self, hr):
        from .zone_profile import speed_for_hr
        return speed_for_hr(self.profile, hr+self.normalization_offset_bpm)

    def hr_for_speed(self, speed):
        from .zone_profile import hr_for_speed
        return hr_for_speed(self.profile, speed)-self.normalization_offset_bpm

    def duration(self, hr):
        return self.curve.inverse(self.speed_for_hr(hr)/3.6)

    def hr_for_duration(self, duration):
        return self.hr_for_speed(self.curve.speed(duration)*3.6)

    def metadata(self, hr):
        zone = self.zone_index(hr)
        row = self.profile["zones"][zone]
        return {"hr_prediction_source": "INDEX" if "INDEX" in row["source"] else row["source"], "hr_prediction_reason":
                "Z5_ACCOUNTING_ESTIMATE_NOT_PHYSIOLOGICAL_INVERSION" if zone == 4 else None,
                "zone": row["zone"]}

    def summary(self):
        from .zone_profile import VERSION as PROFILE_VERSION
        shared = {**self.anchors[3], "zone": "Z5", "hr_bpm": self.bounds[4],
                  "source": "Z4_SHARED_BOUNDARY", "index": None, "count": 0}
        return {"model_version": PROFILE_VERSION, "hr_range_bpm": [self.min_hr, self.max_hr],
                "hr_reference_offset_bpm": self.normalization_offset_bpm,
                "curve_duration_range_s": [self.curve.times[0], self.curve.times[-1]],
                "curve_speed_range_kmh": [self.curve.speed(self.curve.times[-1])*3.6,
                                           self.curve.speed(self.curve.times[0])*3.6],
                "speed_range_kmh": list(self.speed_range), "zones": self.anchors+[shared],
                "conflicting_zones": [], "expert_duration_conflicts": [],
                "equivalence_slope_percent_per_bpm": None,
                "z5_equivalence_slope_percent_per_bpm": None,
                "profile_version": self.profile["parameters_version"], "hr_is_estimate": True}
