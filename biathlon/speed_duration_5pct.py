"""Exact measured windows and bounded outward continuation.

This module never invents observations. The correction is monotone away from
each real anchor; its five-percent bound is a modelling prior, not an error bar.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.interpolate import CubicHermiteSpline, PchipInterpolator


EPSILON = .05
GRID_SIZE = 4097


def _soft_cap(values):
    w = np.clip((values - .04) / .02, 0., 1.)
    return np.where(values <= .04, values,
                    np.where(values >= .06, EPSILON, .04 + .02 * (w - w*w/2)))


def _normative_breaks(reference):
    result = [reference._x[0]]
    for i, (eta, *_rest) in enumerate(reference._pieces):
        lo, hi = reference._x[i:i+2]
        result.extend((lo + eta*(hi-lo), hi - eta*(hi-lo), hi))
    return result


def _strict_polynomial_sign(coefficients, *, positive):
    """Certify polynomials on [0, 1], including points between chart samples.

    Bernstein bounds decide almost every interval without root finding. Only
    an inconclusive bound needs evaluation at the derivative's real roots.
    Coefficients are ascending powers of the normalized interval coordinate.
    """
    coefficients = coefficients if positive else -coefficients
    degree = coefficients.shape[1] - 1
    bernstein = np.zeros_like(coefficients)
    for j in range(degree + 1):
        for k in range(j + 1):
            bernstein[:, j] += coefficients[:, k] * math.comb(j, k) / math.comb(degree, k)
    uncertain = np.flatnonzero(np.min(bernstein, axis=1) <= 0.)
    for idx in uncertain:
        row = coefficients[idx]
        critical = np.polynomial.polynomial.polyroots(np.arange(1, len(row))*row[1:])
        candidates = [0., 1.] + [float(z.real) for z in critical
            if abs(z.imag) <= 1e-9 and 0 < z.real < 1]
        if min(np.polynomial.polynomial.polyval(candidates, row)) <= 0:
            return False
    return True


class _OutwardCorrection:
    def __init__(self, model, orientation, *, grid_size):
        self.orientation = orientation
        self.anchor = model.t1 if orientation == -1 else model.t2
        edge = model.reference.times[0] if orientation == -1 else model.reference.times[-1]
        self.ratio = model.r1 if orientation == -1 else model.r2
        max_u = orientation * math.log(edge/self.anchor)
        self.max_u = max(0., max_u)
        derivative = orientation * (model.raw_log_slope(self.anchor) - model.reference.log_slope(self.anchor))
        self.sign = 0 if abs(derivative) <= 1e-10 else (1 if derivative > 0 else -1)
        if not self.sign and hasattr(model, "neutral_outward_sign"):
            self.sign = model.neutral_outward_sign(orientation)
        self.spline = None
        if max_u <= 1e-14 or not self.sign:
            return
        # Including normative slope breaks makes its slope exactly linear on
        # every Hermite interval, permitting analytic sign certification below.
        breaks = [orientation*(x-math.log(self.anchor)) for x in _normative_breaks(model.reference)]
        u = np.unique(np.concatenate((np.linspace(0., max_u, grid_size),
                                      [x for x in breaks if 0 < x < max_u])))
        times = self.anchor*np.exp(orientation*u)
        base = np.array([model.reference.speed(float(t))*self.ratio for t in times])
        log_ratio = model.continuation_log_speed(times, orientation) - np.log(base)
        # The limiter saturates well before these bounds. Clipping only this
        # intermediate exponent avoids overflow for tightly spaced tests.
        raw = np.expm1(np.clip(log_ratio, -50., 50.))
        raw[0] = 0.
        maximum = np.maximum.accumulate(np.maximum(0., self.sign*raw))
        values = _soft_cap(maximum)
        provisional = PchipInterpolator(u, values)
        tangents = provisional.derivative()(u)
        tangents[0] = abs(derivative)
        self.spline = CubicHermiteSpline(u, values, tangents)
        self.derivative = self.spline.derivative()
        self._validate(model.reference, times, values, tangents)

    def _validate(self, reference, times, values, tangents):
        u = self.spline.x
        widths = np.diff(u)
        delta = np.diff(values)
        # p(w) is correction magnitude on a normalized interval, 0 <= w <= 1.
        p = np.column_stack((values[:-1], tangents[:-1]*widths,
            3*delta-(2*tangents[:-1]+tangents[1:])*widths,
            -2*delta+(tangents[:-1]+tangents[1:])*widths))
        # Monotone Hermite tangents must remain compatible with the prescribed
        # initial derivative. Inspect derivative minima, not only sampled data.
        derivative = p[:, 1:]*np.array([1., 2., 3.])
        minima = np.minimum(derivative[:, 0], derivative.sum(axis=1))
        with np.errstate(divide="ignore", invalid="ignore"):
            vertex = -derivative[:, 1]/(2*derivative[:, 2])
        inside = (vertex > 0) & (vertex < 1) & (derivative[:, 2] > 0)
        minima[inside] = np.minimum(minima[inside], derivative[inside, 0]
            + derivative[inside, 1]*vertex[inside] + derivative[inside, 2]*vertex[inside]**2)
        if np.any(minima < -1e-13) or values.min() < 0 or values.max() > EPSILON + 1e-12:
            raise ValueError("BOUNDED_CORRECTION_NOT_MONOTONE")
        normative = np.array([reference.log_slope(float(t)) for t in times])
        denominator = self.sign*p
        denominator[:, 0] += 1.
        # log slope F = n + orientation*sign*p_u/(1+sign*p).
        # Both inequalities -1 < log slope F < 0 become quartic polynomials.
        numerator = np.zeros((len(widths), 5))
        numerator[:, :4] = normative[:-1, None]*denominator
        numerator[:, 1:] += np.diff(normative)[:, None]*denominator
        numerator[:, :3] += self.orientation*self.sign*derivative/widths[:, None]
        distance_numerator = numerator.copy()
        distance_numerator[:, :4] += denominator
        if not (_strict_polynomial_sign(numerator, positive=False)
                and _strict_polynomial_sign(distance_numerator, positive=True)):
            raise ValueError("BOUNDED_NONPHYSICAL_CONTINUATION")

    def value(self, t):
        if self.spline is None:
            return 0.
        u = min(self.max_u, max(0., self.orientation*math.log(t/self.anchor)))
        return self.sign*float(self.spline(u))

    def log_slope_delta(self, t):
        if self.spline is None:
            return 0.
        u = min(self.max_u, max(0., self.orientation*math.log(t/self.anchor)))
        return self.orientation*self.sign*float(self.derivative(u))/(1+self.sign*float(self.spline(u)))


class TwoAnchorCurve:
    calibration_mode = "TWO_ANCHOR_5PCT"
    model_version = "speed-duration-individual-5pct-v4"

    def __init__(self, reference, pairs, *, grid_size=GRID_SIZE):
        self.reference = reference
        (self.t1, self.v1), (self.t2, self.v2) = pairs
        self.L = math.log(self.t2/self.t1)
        d = math.log(self.v2/self.v1)
        if self.L <= 0 or not -1 < d/self.L < 0:
            raise ValueError("Tests conflict: longer efforts require lower speed and greater distance")
        self.r1 = self.v1/reference.speed(self.t1)
        self.r2 = self.v2/reference.speed(self.t2)
        x = np.linspace(0., self.L, 1001)
        base = np.array([self.base_speed(self.t1*math.exp(float(xx))) for xx in x])
        z = x*(x-self.L)
        self.c = float(np.sum(z*(np.log(base/self.v1)-d*x/self.L))/np.sum(z*z))
        self.a = d/self.L-self.c*self.L
        # Raw log slope is affine: checking its two endpoints is exact.
        if not (-1 < self.a < 0 and -1 < self.a+2*self.c*self.L < 0):
            raise ValueError("TWO_ANCHOR_NONPHYSICAL_TEST_WINDOW")
        self._validate_window_corridor()
        self.left = _OutwardCorrection(self, -1, grid_size=grid_size)
        self.right = _OutwardCorrection(self, 1, grid_size=grid_size)
        self.times = tuple(sorted(set((*reference.times, self.t1, self.t2))))
        self._x = tuple(math.log(t) for t in self.times)
        self.speeds = tuple(self.speed(t) for t in self.times)

    def raw_log_slope(self, t):
        return self.a+2*self.c*math.log(t/self.t1)

    def continuation_log_speed(self, times, orientation):
        x = np.log(times/self.t1)
        return math.log(self.v1) + self.a*x + self.c*x*x

    def base_speed(self, t):
        w = max(0., min(1., math.log(t/self.t1)/self.L))
        return self.reference.speed(t)*math.exp((1-w)*math.log(self.r1)+w*math.log(self.r2))

    def _validate_window_corridor(self):
        # ln(E/B) is piecewise quadratic. Its extrema occur at the boundaries
        # or where the affine difference of log slopes crosses zero.
        breaks = sorted(set([math.log(self.t1), math.log(self.t2)] + [x for x in
            _normative_breaks(self.reference) if math.log(self.t1) < x < math.log(self.t2)]))
        ratio_slope = math.log(self.r2/self.r1)/self.L
        for lo, hi in zip(breaks, breaks[1:]):
            def slope(x):
                t = math.exp(x)
                return self.raw_log_slope(t)-self.reference.log_slope(t)-ratio_slope
            a, b = slope(lo), slope(hi)
            candidates = [lo, hi]
            if a*b < 0:
                candidates.append(lo-a*(hi-lo)/(b-a))
            for x in candidates:
                t = math.exp(x); local = math.log(t/self.t1)
                ratio = self.v1*math.exp(self.a*local+self.c*local*local)/self.base_speed(t)
                if not 1-EPSILON-1e-12 <= ratio <= 1+EPSILON+1e-12:
                    raise ValueError("TWO_ANCHOR_TEST_WINDOW_EXCEEDS_CORRIDOR")

    def speed(self, t):
        # Canonical validation rejects booleans, nonfinite values and times
        # outside the same fixed duration domain as the normative function.
        self.reference.speed(t)
        t = min(self.reference.times[-1], max(self.reference.times[0], t))
        if t == self.t1:
            return self.v1
        if t == self.t2:
            return self.v2
        if self.t1 <= t <= self.t2:
            x = math.log(t/self.t1)
            return self.v1*math.exp(self.a*x+self.c*x*x)
        side = self.left if t < self.t1 else self.right
        return self.reference.speed(t)*side.ratio*(1+side.value(t))

    def distance(self, t):
        return t*self.speed(t)

    def log_slope(self, t):
        self.reference.speed(t)
        if self.t1 <= t <= self.t2:
            return self.raw_log_slope(t)
        return self.reference.log_slope(t)+(self.left if t < self.t1 else self.right).log_slope_delta(t)

    def inverse(self, value, *, distance=False):
        # Reuse canonical bracketed inversion and explicit domain behaviour.
        from .speed_duration import Curve
        return Curve.inverse(self, value, distance=distance)

    def point_metadata(self, t):
        correction = self.speed(t)/self.base_speed(t)-1
        extrapolated = t < self.t1 or t > self.t2
        return {"calibration_mode": self.calibration_mode, "extrapolated": extrapolated,
            "within_test_window": not extrapolated, "additional_correction": correction,
            "extrapolation_capped": extrapolated and abs(correction) >= EPSILON-1e-10}


class MultipointCurve:
    """C1 interpolation of every real test, continuing its terminal pieces.

    The existing Curve interpolation is fitted only inside the measured
    window. Its terminal log-quadratic pieces define the raw continuation;
    synthetic normative knots never influence the fitted endpoint slopes.
    Each side then uses the same no-reversal, smooth 5% limiter as two tests.
    """
    calibration_mode = "MULTIPOINT_C1_5PCT"
    model_version = "speed-duration-individual-5pct-v4"

    def __init__(self, reference, pairs, *, grid_size=GRID_SIZE):
        from .speed_duration import Curve
        if len(pairs) < 3:
            raise ValueError("Multipoint mode requires at least three real tests")
        self.reference = reference
        self.window = Curve(tuple(t for t, _ in pairs), tuple(v for _, v in pairs))
        self.anchors = dict(pairs)
        self.t1, self.v1 = pairs[0]
        self.t2, self.v2 = pairs[-1]
        self.r1 = self.v1/reference.speed(self.t1)
        self.r2 = self.v2/reference.speed(self.t2)
        self._tails = {}
        self._log_ratios = tuple(math.log(v/reference.speed(t)) for t, v in pairs)
        for orientation, i, anchor, speed in [(-1, 0, self.t1, self.v1), (1, -1, self.t2, self.v2)]:
            eta, plateau, left, right = self.window._pieces[i]
            width = (self.window._x[1]-self.window._x[0] if orientation == -1
                     else self.window._x[-1]-self.window._x[-2])
            slope = left if orientation == -1 else right
            curvature = (plateau-left if orientation == -1 else right-plateau)/(eta*width)
            self._tails[orientation] = (anchor, speed, slope, curvature)
        self.left = _OutwardCorrection(self, -1, grid_size=grid_size)
        self.right = _OutwardCorrection(self, 1, grid_size=grid_size)
        self.times = tuple(sorted(set((*reference.times, *self.window.times))))
        self._x = tuple(math.log(t) for t in self.times)
        self.speeds = tuple(self.speed(t) for t in self.times)

    def continuation_log_speed(self, times, orientation):
        anchor, speed, slope, curvature = self._tails[orientation]
        x = np.log(times/anchor)
        return math.log(speed) + slope*x + curvature*x*x/2

    def raw_log_slope(self, t):
        if self.t1 <= t <= self.t2:
            return self.window.log_slope(t)
        anchor, _, slope, curvature = self._tails[-1 if t < self.t1 else 1]
        return slope + curvature*math.log(t/anchor)

    def neutral_outward_sign(self, orientation):
        # Equal tangents can still have a nonzero initial quadratic departure.
        # The normative slope is affine up to its next break, so this quotient
        # gives that piece's curvature without an arbitrary probing distance.
        anchor, _, _, curvature = self._tails[orientation]
        x = math.log(anchor)
        outward = [orientation*(b-x) for b in _normative_breaks(self.reference)
                   if orientation*(b-x) > 1e-12]
        if not outward:
            return 0
        h = orientation*min(outward)/2
        normative_curvature = (self.reference.log_slope(anchor*math.exp(h))
                               - self.reference.log_slope(anchor))/h
        delta = curvature-normative_curvature
        return 0 if abs(delta) <= 1e-10 else (1 if delta > 0 else -1)

    def base_speed(self, t):
        # Inside the measured window, the comparison baseline interpolates
        # anchor ratios; outside, each extreme's ratio is held fixed. Interior
        # measurements are never constrained by an endpoint-only corridor.
        log_ratio = np.interp(math.log(t), self.window._x, self._log_ratios)
        return self.reference.speed(t)*math.exp(float(log_ratio))

    def speed(self, t):
        self.reference.speed(t)
        t = min(self.reference.times[-1], max(self.reference.times[0], t))
        if t in self.anchors:
            return self.anchors[t]
        if self.t1 <= t <= self.t2:
            return self.window.speed(t)
        side = self.left if t < self.t1 else self.right
        return self.reference.speed(t)*side.ratio*(1+side.value(t))

    def distance(self, t):
        return t*self.speed(t)

    def log_slope(self, t):
        self.reference.speed(t)
        if self.t1 <= t <= self.t2:
            return self.window.log_slope(t)
        return self.reference.log_slope(t)+(self.left if t < self.t1 else self.right).log_slope_delta(t)

    def inverse(self, value, *, distance=False):
        from .speed_duration import Curve
        return Curve.inverse(self, value, distance=distance)

    def point_metadata(self, t):
        correction = self.speed(t)/self.base_speed(t)-1
        extrapolated = t < self.t1 or t > self.t2
        return {"calibration_mode": self.calibration_mode, "extrapolated": extrapolated,
            "within_test_window": not extrapolated, "additional_correction": correction,
            "extrapolation_capped": extrapolated and abs(correction) >= EPSILON-1e-10}
