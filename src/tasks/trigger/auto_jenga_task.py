"""祖赞卡之梯：观测悬挂图腾，预测着陆时的塔顶，单次释放后等待新图腾。"""

import logging
import math
import threading
import time

import numpy as np
import win32gui
from ok import CaptureException, TriggerTask

from src.core.base_game_task import BaseGameTask
from src.icons import Icons
from src.image.jenga_detector import JengaDetector

logger = logging.getLogger(__name__)


def _fit_figure_eight_phase(points, horizontal_geometry=None, use_vertical=True):
    points = np.asarray(points)
    times = points[:, 0]
    xy = points[:, 1:3]
    # Occlusion may hide an entire turning point. Observed min/max would then
    # underestimate the radius and trap geometric fitting in the wrong branch.
    # Use the native clock for initialization, then refine each phase spatially.
    relative = times - times[0]
    design = np.column_stack((np.ones(len(times)), np.sin(1.6 * relative), np.cos(1.6 * relative)))
    coeff = np.linalg.lstsq(design, xy[:, 0], rcond=None)[0]
    cx, amplitude = coeff[0], np.hypot(coeff[1], coeff[2])
    cy = np.mean(np.quantile(xy[:, 1], [0.005, 0.995]))
    vertical = amplitude * 0.32
    phase = np.linspace(-np.pi, np.pi, 1024, endpoint=False)
    s1 = np.sin(phase)
    s2 = np.sin(2 * phase)
    fitted = 1.6 * relative + np.arctan2(coeff[2], coeff[1])
    if horizontal_geometry is not None:
        cx, amplitude = horizontal_geometry
        vertical = amplitude * 0.32
        cost = np.sum((cx + amplitude * np.sin(1.6 * relative[:, None] + phase[None, :]) - xy[:, 0, None]) ** 2, axis=0)
        fitted = 1.6 * relative + phase[np.argmin(cost)]
        # The camera follows every new tower layer. Its vertical center must
        # be measured afresh, even when the horizontal orbit stays unchanged.
        cy = np.median(xy[:, 1] + vertical * np.sin(2 * fitted))
    for _ in range(6):
        if not use_vertical:
            # The two inverse-sine branches solve horizontal position exactly.
            # Preserve the native clock's branch through centers, turns and
            # missing frames without searching 1024 angles for every sample.
            first = np.arcsin(np.clip((xy[:, 0] - cx) / max(amplitude, 1e-6), -1.0, 1.0))
            second = np.pi - first
            a = (first - fitted + np.pi) % (2 * np.pi) - np.pi
            b = (second - fitted + np.pi) % (2 * np.pi) - np.pi
            fitted += np.clip(np.where(abs(a) <= abs(b), a, b), -0.45, 0.45)
        else:
            cost = (cx + amplitude * s1[None, :] - xy[:, 0, None]) ** 2
            cost += (cy - vertical * s2[None, :] - xy[:, 1, None]) ** 2
            angular = (phase[None, :] - fitted[:, None] + np.pi) % (2 * np.pi) - np.pi
            cost += np.where(abs(angular) < 0.45, 0, 1e8)
            indices = np.argmin(cost, axis=1)
            fitted = np.unwrap(phase[indices])
            for i in range(1, len(fitted)):
                if abs(fitted[i] - fitted[i - 1] - 1.6 * (times[i] - times[i - 1])) > 1:
                    allowed = (
                        abs(np.angle(np.exp(1j * (phase - fitted[i - 1] - 1.6 * (times[i] - times[i - 1]))))) < 0.5
                    )
                    indices[i] = np.argmin(np.where(allowed, cost[i], np.inf))
                    expected = fitted[i - 1] + 1.6 * (times[i] - times[i - 1])
                    fitted[i] = expected + np.angle(np.exp(1j * (phase[indices[i]] - expected)))
        for _ in range(2):
            dx = cx + amplitude * np.sin(fitted) - xy[:, 0]
            dy = cy - vertical * np.sin(2 * fitted) - xy[:, 1]
            vx = amplitude * np.cos(fitted)
            vy = -2 * vertical * np.cos(2 * fitted)
            if not use_vertical:
                dy = vy = 0.0
            fitted -= np.clip((dx * vx + dy * vy) / np.maximum(vx * vx + vy * vy, 1e-6), -0.01, 0.01)
        design = np.column_stack((np.ones(len(points)), np.sin(fitted)))
        cx, amplitude = (
            np.linalg.lstsq(design, xy[:, 0], rcond=None)[0] if horizontal_geometry is None else horizontal_geometry
        )
        vertical = 0.32 * amplitude
        cy = np.median(xy[:, 1] + vertical * np.sin(2 * fitted))
    residual = np.hypot(cx + amplitude * np.sin(fitted) - xy[:, 0], cy - vertical * np.sin(2 * fitted) - xy[:, 1])
    return fitted, (cx, amplitude, cy, vertical), residual


class JengaPlayer:
    """Fit the observed figure-eight and predict contact under capped gravity.

    All coordinates are normalized screen pixels and all times are frame-generation
    perf_counter timestamps. Native equations are documented in jenga_judgment.md.
    """

    def __init__(self, max_delay=0.16, greedy_high_sway=False, greedy_release=False):
        self.max_delay = max_delay
        self.greedy_high_sway = greedy_high_sway
        self.greedy_release = greedy_release
        self.release_delay = 0.0
        self.history = []
        self.released_at = None
        self.released_piece = None
        self.missing_since = None
        self.saw_absence = False
        self.placement_confirmed = False
        self.last_seen = -math.inf
        self.prediction = None
        self.horizontal_geometry = None
        self.handoff_direction = None
        self.handoff_target_at = None
        self.handoff_window = None
        self.handoff_waited = False

    def mark_released(self, now, piece):
        self.handoff_target_at = None
        self.handoff_window = None
        self.handoff_waited = False
        self.released_at = now
        self.released_piece = piece
        self.missing_since = None
        self.saw_absence = False
        self.placement_confirmed = False
        self.history.clear()

    def confirm_placement(self):
        # Judgment polling consumes the frames where the old piece disappears.
        # Two independent success frames prove contact and allow the new bird's
        # piece to rearm even when it spawns near the previous release position.
        self.placement_confirmed = True

    def reset_motion(self):
        self.history.clear()
        self.prediction = None
        self.horizontal_geometry = None
        self.handoff_direction = None
        self.handoff_target_at = None
        self.handoff_window = None
        self.handoff_waited = False

    @staticmethod
    def good_margin(carried_kind, tower_kind, scale):
        """Conservative horizontal boundary for half the smaller face area."""
        faces = {
            "monkey": (0.40, 0.43, -0.01),
            "owl": (0.40, 0.42, -0.041),
            "rabbit": (0.36, 0.40, -0.01),
        }
        a = faces.get(carried_kind, (0.36, 0.40, None))
        b = faces.get(tower_kind, (0.36, 0.40, None))
        # Extracted centers range from 0 to -0.041. Unknown shapes retain a
        # wider separation allowance rather than assuming perfectly aligned Z.
        separation = abs(a[2] - b[2]) if a[2] is not None and b[2] is not None else 0.045
        depth_overlap = min(a[1], b[1], (a[1] + b[1]) / 2 - separation)
        required_overlap = 0.5 * min(a[0] * a[1], b[0] * b[1]) / depth_overlap
        # Leave projection room for the small native tower tilt.
        return max(0.0, (a[0] + b[0]) / 2 - required_overlap) * scale * 0.98

    @staticmethod
    def _harmonic(points, column, omega):
        t = points[:, 0] - points[-1, 0]
        design = np.column_stack((np.ones(len(t)), np.sin(omega * t), np.cos(omega * t)))
        values = points[:, column]
        coeff = np.linalg.lstsq(design, values, rcond=None)[0]
        # Limit influence of single-frame template shifts and fixed-update jitter.
        for _ in range(3):
            residual = abs(design @ coeff - values)
            weights = np.minimum(1, 3 / np.maximum(residual, 0.1))
            coeff = np.linalg.lstsq(design * weights[:, None], values * weights, rcond=None)[0]
        error = float(np.quantile(abs(design @ coeff - values), 0.9))
        return coeff, error

    @staticmethod
    def _value(coeff, omega, t):
        return float(coeff @ [1, math.sin(omega * t), math.cos(omega * t)])

    @staticmethod
    def _velocity(coeff, omega, t):
        return float(omega * (coeff[1] * math.cos(omega * t) - coeff[2] * math.sin(omega * t)))

    @staticmethod
    def fall_duration(gap, pixels_per_unit):
        # Native FixedUpdate changes velocity before the physics step. Solve
        # that piecewise-linear fall, including the final partial movement.
        distance = max(0.0, gap / pixels_per_unit)
        step, gravity, terminal = 1 / 30, 49.05, 10.0
        accelerating_steps = math.floor(terminal / (gravity * step))
        accelerating_distance = gravity * step**2 * accelerating_steps * (accelerating_steps + 1) / 2
        if distance >= accelerating_distance:
            return accelerating_steps * step + (distance - accelerating_distance) / terminal
        steps = max(1, math.ceil((math.sqrt(1 + 8 * distance / (gravity * step**2)) - 1) / 2))
        before = gravity * step**2 * (steps - 1) * steps / 2
        return (steps - 1) * step + (distance - before) / (gravity * steps * step)

    def update(self, observation, observed_at, now, input_lead=0.045):
        self.release_delay = 0.0
        self.prediction = None
        if self.handoff_target_at is not None and now > self.handoff_target_at + self.max_delay:
            self.handoff_target_at = None
            self.handoff_window = None
        age = now - observed_at
        lead_center = input_lead
        if observed_at <= self.last_seen or not 0 <= age <= 0.18:
            return False
        self.last_seen = observed_at
        if observation is None or observation.bird is None:
            if self.missing_since is None:
                self.missing_since = observed_at
            if observed_at - self.missing_since >= 0.12:
                self.saw_absence = True
            return False
        carried, tower = observation.carried, observation.tower
        self.missing_since = None
        if self.released_at is not None:
            if now - self.released_at < 0.8:
                return False
            if not (self.saw_absence or self.placement_confirmed) and abs(carried.x - self.released_piece.x) < max(
                120, carried.width
            ):
                return False
            # BeginMovement resets the new bird's phase. Observe its first
            # half stroke after confirmed contact instead of assuming a screen
            # direction or a particular prefab rotation. Use that receiver
            # direction to rank already safe handoffs; the two independent
            # clocks must not veto a full Good contact for many orbits.
            if self.placement_confirmed and self.horizontal_geometry is not None and now - self.released_at <= 1.6:
                center, amplitude = self.horizontal_geometry
                displacement = observation.bird.x - center
                if abs(displacement) > 0.2 * amplitude:
                    self.handoff_direction = math.copysign(1.0, displacement)
            self.released_at = None
            self.history.clear()
        if self.history:
            previous = self.history[-1]
            camera_jump = abs(tower.top - previous[4]) > 35 and abs(tower.bottom - previous[7]) > 35
            if observed_at - previous[0] > 2 * math.pi / 1.6:
                # A partial-orbit occlusion can keep the verified native
                # clock. Beyond one orbit, refit fresh short-arc data under
                # the same cross-checks and projection/error reserves.
                # Keep the already measured horizontal orbit in either case.
                # A frozen frame or scene exit still clears every prior.
                self.history.clear()
            elif camera_jump:
                # The camera follows a new layer vertically. Retain only the
                # horizontal orbit; refit height and tower motion from scratch.
                self.history.clear()
        self.history.append(
            (
                observed_at,
                carried.x,
                carried.bottom,
                tower.x,
                tower.top,
                observation.bird.x,
                observation.bird.bottom,
                tower.bottom,
            )
        )
        self.history = [row for row in self.history if observed_at - row[0] < 6.0]
        # A half-period identifies the native harmonics without requiring a
        # complete orbit for every new piece. Keep the sample and residual gates.
        span = self.history[-1][0] - self.history[0][0]
        short = span < 2.0
        if len(self.history) < (6 if short else 12) or span < 0.6 or (short and self.horizontal_geometry is None):
            return False
        points = np.asarray(self.history)
        if np.ptp(points[:, 5]) < 100:
            return False
        x_offset = float(np.median(points[:, 1] - points[:, 5]))
        # The header and lighting can move the neck's vertical color edge.
        # Recover phase from the bird's horizontal orbit, then measure the
        # complete sculpture's vertical anchor without feeding edge jitter
        # back into phase and inherited horizontal velocity.
        phases, geometry, _ = _fit_figure_eight_phase(
            points[:, [0, 5, 2]], self.horizontal_geometry if short else None, use_vertical=False
        )
        center, amplitude, vertical_center, vertical_amplitude = geometry
        phase = phases[-1]
        offsets = points[:, 2] + vertical_amplitude * np.sin(2 * phases)
        y_offset = float(np.median(offsets))
        consistent = abs(offsets - y_offset) <= 25
        # A cached torso or a sparkle can shift the detected edge without
        # moving the hanging body. Fit its attachment only from a clear
        # majority, and never release on a frame outside that same cluster.
        if not consistent[-1] or np.count_nonzero(consistent) < 6 or np.mean(consistent) < 0.6:
            return False
        vertical_center = float(np.median(offsets[consistent]))
        phases = phases[consistent]
        points = points[consistent]
        error_x = float(np.quantile(abs(center + amplitude * np.sin(phases) - points[:, 5]), 0.9))
        error_y = float(np.quantile(abs(vertical_center - vertical_amplitude * np.sin(2 * phases) - points[:, 2]), 0.9))
        geometric_bird_x = np.array([center, amplitude * math.cos(phase), amplitude * math.sin(phase)])
        bird_x = geometric_bird_x.copy()
        bird_x[0] += x_offset
        bird_y = np.array(
            [
                vertical_center,
                -vertical_amplitude * math.cos(2 * phase),
                -vertical_amplitude * math.sin(2 * phase),
            ]
        )
        phase_time = (phases - phase) / 1.6
        phase_points = points.copy()
        phase_points[:, 0] = phase_time
        offset_error = float(np.quantile(abs(points[:, 1] - points[:, 5] - x_offset), 0.7))
        if not short:
            # The rendered sculpture moves relative to the neck during the
            # orbit. A constant attachment mixes opposite sides of that motion
            # into its error budget and also biases the predicted release.
            attachment_points = np.column_stack((phase_time, points[:, 1] - points[:, 5]))
            attachment, attachment_error = self._harmonic(attachment_points, 1, 1.6)
            if np.hypot(attachment[1], attachment[2]) <= 0.15 * amplitude / 1.25 and attachment_error < offset_error:
                bird_x = geometric_bird_x + attachment
                offset_error = attachment_error
        # A new contour can shift on the very release frame while a historical
        # quantile still looks precise. Budget that current disagreement too;
        # never release from an attachment fit contradicted by the latest body.
        # A discrepant release-frame contour is also evidence that the prior
        # attachment fit may be biased in the opposite direction. Count both
        # sides of that disagreement rather than only the historical spread.
        offset_error = max(offset_error, 2 * abs(carried.x - self._value(bird_x, 1.6, 0.0)))
        # Tower animation advances on Update, the bird on FixedUpdate. Compare
        # both predictions only when each explains its own observed history.
        # At high layers an invalid shared clock otherwise vetoes accurate
        # wall-clock forecasts (verified against later real captured positions).
        tower_geometric, geometric_error = self._harmonic(phase_points, 3, math.pi / 2)
        tower_x, error_tower = self._harmonic(points, 3, math.pi / 2)
        scale = amplitude / 1.25
        if not 150 < scale < 300 or max(error_x, error_tower) > 5:
            return False
        # A shifting anchor can explain small pose changes. Disagreement larger
        # than a quarter of the piece height means the hanging geometry itself
        # is unreliable, even at a crossing with low horizontal velocity.
        if error_y > (carried.bottom - carried.top) / 4:
            return False
        if not short:
            self.horizontal_geometry = geometry[:2]

        # The receiver's clock is measured separately from the bird. A short
        # fit retains its projection error below and needs a measured orbit.
        # Known native feet-to-face distances also recover the receiving plane
        # when the owl's crown is missing from its tracked upper texture.
        # Recover the first swept contact from the complete native geometry.
        receiver_height = {"monkey": 0.755, "owl": 0.679}.get(tower.kind)
        held_height = (carried.bottom - carried.top) / scale
        complete_held = (carried.kind != "rabbit" and 0.73 <= held_height <= 0.78) or (
            carried.kind == "rabbit" and 0.84 <= held_height <= 0.98
        )
        measured_contact = bool(
            (not short or (self.greedy_release and span >= 0.8))
            and (
                self.greedy_release
                or (self.greedy_high_sway and np.hypot(tower_x[1], tower_x[2]) >= 0.6 * scale)
            )
            and receiver_height is not None
            and (0.62 if tower.kind == "owl" else 0.70) <= (tower.bottom - tower.top) / scale <= 0.86
            and complete_held
            and min(carried.confidence, tower.confidence) >= 0.75
            and error_y <= 0.04 * scale
            and offset_error <= 0.03 * scale
        )
        contact_clock_step = 0.0 if measured_contact else 1 / 60
        contact_center_step = 0.0 if measured_contact else 1 / 60
        contact_top = tower.bottom - receiver_height * scale if measured_contact else tower.top
        foot_offset = (0.005 if carried.kind in ("monkey", "rabbit") else 0.009) * scale if measured_contact else 0.0

        def landing(delay, flight_error=0.0, contact_tick_error=0.0):
            release_time = age + lead_center + delay
            bottom = self._value(bird_y, 3.2, release_time)
            # The native controller sweeps to first contact in movement
            # substeps. Known receiving planes use that contact as the nominal
            # time; uncertain crops retain the full fixed-step center.
            duration = max(
                0.0,
                self.fall_duration(contact_top - bottom + foot_offset, scale) + contact_center_step + flight_error,
            )
            x = self._value(bird_x, 1.6, release_time)
            x += self._velocity(bird_x, 1.6, release_time) * duration
            # An uncertain flight lasts longer/shorter for both bodies: the
            # sculpture keeps its horizontal velocity while the receiver
            # keeps swaying. The receiver's physics tick is separate.
            target = self._value(tower_x, math.pi / 2, release_time + duration + contact_clock_step + contact_tick_error)
            return x - target, duration

        left, right = 0.0, self.max_delay
        if right <= 0:
            return False
        if landing(left)[0] * landing(right)[0] < 0:
            for _ in range(15):
                middle = (left + right) / 2
                if landing(left)[0] * landing(middle)[0] <= 0:
                    right = middle
                else:
                    left = middle
            self.release_delay = (left + right) / 2
        else:
            # Good permits a nonzero center offset. A large tower can move
            # alongside the bird without their predicted centers crossing for
            # many cycles. Still test the closest landing against the same
            # complete contact-error budget below; never require a root first.
            delays = np.linspace(left, right, 17)
            self.release_delay = float(min(delays, key=lambda delay: abs(landing(delay)[0])))
        phase_jitter = float(np.quantile(abs(np.diff(phases) - 1.6 * np.diff(points[:, 0])), 0.9)) / 1.6
        # Preserve the configured rendered-motion compensation. The effective
        # free-flight clock includes Rigidbody interpolation, not just the
        # Windows key-processing interval. Budget variation around the lead
        # rather than dividing this existing compensation in half.
        timing_uncertainty = max(1 / 60, input_lead - 1 / 30, phase_jitter / 2)
        # Native contact starts at the first sweep and can finish at the end of
        # that movement substep. It cannot precede first contact by half a full
        # physics step. Keep the small contact margin on the early side and the
        # complete substep on the late side. Uncertain crops keep both sides of
        # the original fixed-step reserve.
        impact_speed = min(10.0, 49.05 * landing(self.release_delay)[1])
        anchor_reserve = (0.01 if measured_contact else 0.08) / max(1.0, impact_speed)
        contact_reserve = max(1 / 60, 0.15 / max(1.0, impact_speed)) if measured_contact else 1 / 60
        flight_uncertainty = max(contact_reserve + anchor_reserve, phase_jitter / 2)
        release_jitter = np.array([-timing_uncertainty, 0.0, timing_uncertainty])
        flight_early = (
            anchor_reserve + max(0.0, phase_jitter / 2 - contact_reserve)
            if measured_contact
            else flight_uncertainty
        )
        flight_jitter = np.array([-flight_early, 0.0, flight_uncertainty])
        contact_jitter = np.array([-contact_clock_step, 0.0, contact_clock_step])

        def contact_bound(delay):
            releases = age + lead_center + delay + release_jitter
            angles = 1.6 * releases
            bottoms = bird_y[0] + bird_y[1] * np.sin(2 * angles) + bird_y[2] * np.cos(2 * angles)
            falls = np.array([self.fall_duration(contact_top - bottom + foot_offset, scale) for bottom in bottoms])
            falls += contact_center_step
            durations = np.maximum(0.0, falls[:, None] + flight_jitter)
            positions = bird_x[0] + bird_x[1] * np.sin(angles) + bird_x[2] * np.cos(angles)
            velocities = 1.6 * (bird_x[1] * np.cos(angles) - bird_x[2] * np.sin(angles))
            falling_x = positions[:, None, None] + velocities[:, None, None] * durations[:, :, None]
            contacts = releases[:, None, None] + durations[:, :, None] + contact_clock_step + contact_jitter
            receivers = (
                tower_x[0] + tower_x[1] * np.sin(math.pi / 2 * contacts) + tower_x[2] * np.cos(math.pi / 2 * contacts)
            )
            # Each flight duration is shared by the sculpture drift and the
            # receiver clock. Unmeasured receivers retain the separate tick.
            return float(np.max(abs(falling_x - receivers)))

        # The closest nominal center is not always the safest release. Near a
        # turning point, a nearby Good landing may have a smaller complete
        # timing envelope. Compare that envelope without widening the margin.
        candidates = [self.release_delay, *np.linspace(0.0, self.max_delay, 17)]
        self.release_delay = float(min(candidates, key=contact_bound))
        left = max(0.0, self.release_delay - self.max_delay / 16)
        right = min(self.max_delay, self.release_delay + self.max_delay / 16)
        for _ in range(8):
            a, b = (2 * left + right) / 3, (left + 2 * right) / 3
            if contact_bound(a) < contact_bound(b):
                right = b
            else:
                left = a
        refined = (left + right) / 2
        if contact_bound(refined) < contact_bound(self.release_delay):
            self.release_delay = refined
        # A short arc needs an extra reserve. Fade it out over the remaining
        # short-observation interval: a single new frame at 1.2 seconds must
        # not suddenly remove 22 px of protection on a fast crossing.
        short_reserve = 22.0 * min(1.0, max(0.0, (2.0 - span) / 0.8)) if short else 0.0
        if measured_contact:
            # Complete native anchors remove the generic unknown-body reserve.
            # The fitted short arc still keeps its extrapolation covariance,
            # observed residuals and the complete release/flight envelope.
            short_reserve = 0.0
        inverse_design = None
        if short:
            design = np.column_stack(
                (
                    np.ones(len(points)),
                    np.sin(math.pi / 2 * (points[:, 0] - observed_at)),
                    np.cos(math.pi / 2 * (points[:, 0] - observed_at)),
                )
            )
            inverse_design = np.linalg.pinv(design)
        timed_points = points[points[:, 0] > observed_at - 3.5]
        timed_bird, _ = self._harmonic(timed_points, 5, 1.6)

        def candidate_error(delay, forecast=False):
            release = age + lead_center + delay
            duration = landing(delay)[1]
            contact = release + duration + contact_clock_step
            tower_clock_error = (
                abs(self._value(tower_x, math.pi / 2, contact) - self._value(tower_geometric, math.pi / 2, contact))
                if geometric_error <= 5
                else 0.0
            )
            bird_clock_error = abs(self._value(geometric_bird_x, 1.6, release) - self._value(timed_bird, 1.6, release))
            if not forecast and tower_clock_error > 8:
                return math.inf
            if not forecast and bird_clock_error > 6:
                return math.inf
            # A far handoff only requests a later observation. Account for its
            # clock disagreement as extra uncertainty; actual input keeps both
            # original hard cross-checks under newly observed geometry.
            clock_reserve = max(0.0, tower_clock_error - 8) + max(0.0, bird_clock_error - 6) if forecast else 0.0
            fit_budget = 2 * (error_x + error_tower)
            if inverse_design is not None:
                projection = np.array([1, math.sin(math.pi / 2 * contact), math.cos(math.pi / 2 * contact)])
                tower_projection_error = max(1.0, error_tower) * np.sum(abs(projection @ inverse_design))
                fit_budget = 2 * error_x + max(2 * error_tower, tower_projection_error)
            impact_speed = min(10 * scale, 49.05 * scale * duration)
            relative = self._velocity(bird_x, 1.6, release) - self._velocity(tower_x, math.pi / 2, contact)
            return (
                contact_bound(delay)
                + max(short_reserve, fit_budget)
                + offset_error
                + error_y * abs(relative) / max(1, impact_speed)
                + clock_reserve
            )

        # Small allowed offsets can accumulate into a tower far outside the
        # bird's orbit. Use feedback to steer toward its measured center, but
        # only among candidates satisfying the complete unchanged Good guard.
        displacement = center - tower_x[0]
        correction = math.copysign(max(0.0, abs(displacement) - 0.025 * scale), displacement)
        desired_offset = float(np.clip(0.65 * correction, -0.055 * scale, 0.055 * scale))
        left, right = 0.0, self.max_delay
        if (landing(left)[0] - desired_offset) * (landing(right)[0] - desired_offset) < 0:
            for _ in range(15):
                middle = (left + right) / 2
                if (landing(left)[0] - desired_offset) * (landing(middle)[0] - desired_offset) <= 0:
                    right = middle
                else:
                    left = middle
            candidates.append((left + right) / 2)
        candidates.append(self.release_delay)
        landing_margin = self.good_margin(carried.kind, tower.kind, scale)
        greedy = bool(self.greedy_release or (self.greedy_high_sway and np.hypot(tower_x[1], tower_x[2]) >= 0.6 * scale))
        if greedy:
            self.handoff_target_at = None
            self.handoff_window = None
        if self.handoff_target_at is not None:
            remaining = max(0.0, self.handoff_target_at - now)
            # A scheduled handoff is a prediction, not permission to discard
            # later safe input windows. Cancel it as soon as fresh geometry
            # no longer supports its full contact budget.
            if candidate_error(remaining, forecast=remaining > self.max_delay) > landing_margin:
                if self.handoff_window is None:
                    self.handoff_window = (self.handoff_target_at - 0.125, self.handoff_target_at + 0.125)
                lower, upper = (max(0.0, at - now) for at in self.handoff_window)

                def handoff_error(delay):
                    return candidate_error(delay, forecast=delay > self.max_delay)

                delays = np.linspace(lower, upper, 17)
                best = float(min(delays, key=handoff_error))
                left = max(lower, best - (upper - lower) / 16)
                right = min(upper, best + (upper - lower) / 16)
                for _ in range(8):
                    a, b = (2 * left + right) / 3, (left + 2 * right) / 3
                    if handoff_error(a) < handoff_error(b):
                        right = b
                    else:
                        left = a
                refined = (left + right) / 2
                if handoff_error(refined) < handoff_error(best):
                    best = refined
                if handoff_error(best) <= landing_margin:
                    self.handoff_target_at = now + best
                else:
                    self.handoff_target_at = None
                    self.handoff_window = None
        safe = [(float(delay), candidate_error(delay)) for delay in candidates]
        safe = [(delay, error) for delay, error in safe if error <= landing_margin]
        if not safe:
            return False

        # The first safe fast crossing can be outward, just before the
        # corrective root enters the input horizon. Wait only for that same
        # nearby crossing, with a bounded extra 200 ms and a full forecast.
        if not greedy and not short and self.handoff_target_at is None:
            current = min(safe, key=lambda item: abs(landing(item[0])[0] - desired_offset))
            left, right = self.max_delay, self.max_delay + 0.2
            if (landing(left)[0] - desired_offset) * (landing(right)[0] - desired_offset) < 0:
                for _ in range(15):
                    middle = (left + right) / 2
                    if (landing(left)[0] - desired_offset) * (landing(middle)[0] - desired_offset) <= 0:
                        right = middle
                    else:
                        left = middle
                delay = (left + right) / 2
                if (
                    abs(landing(delay)[0] - desired_offset) + 5 < abs(landing(current[0])[0] - desired_offset)
                    and candidate_error(delay, forecast=True) <= landing_margin
                ):
                    return False

        self.route_debug = None
        if not greedy and self.handoff_direction is not None and np.hypot(tower_x[1], tower_x[2]) >= 0.3 * scale:
            # A fast next crossing can stay inside Good for less than 50 ms.
            # Resolve that opportunity before comparing the two handoffs.
            taus = np.arange(1.4, 5.41, 0.0125)
            release_offsets = taus[:, None] + np.array([-timing_uncertainty, 0.0, timing_uncertainty])
            future_x = center + x_offset + self.handoff_direction * amplitude * np.sin(1.6 * release_offsets)
            future_v = self.handoff_direction * amplitude * 1.6 * np.cos(1.6 * release_offsets)
            future_bottom = vertical_center - vertical_amplitude * np.sin(3.2 * release_offsets)
            mean_top = np.median(points[:, 4])
            gaps = mean_top - future_bottom[None, :, :] + scale * np.array([-0.16, 0.0, 0.06])[:, None, None]
            fall = np.array([self.fall_duration(float(gap), scale) for gap in gaps.flat]).reshape(gaps.shape) + 1 / 60
            flights = fall[:, :, :, None] + np.array([-flight_uncertainty, 0.0, flight_uncertainty])
            future_landing = future_x[None, :, :, None] + future_v[None, :, :, None] * flights
            tower_amp = np.hypot(tower_x[1], tower_x[2])
            gain = (tower_amp + 0.10 * scale) / tower_amp
            next_margin = self.good_margin("", carried.kind, scale)
            observation_span = np.maximum(0.0, taus - 0.75)
            next_reserve = 22.0 * np.clip((2.0 - observation_span) / 0.8, 0.0, 1.0)
            next_fit = max(4.0, 2 * (error_x + error_tower))
            spawn_uncertainty = timing_uncertainty + flight_uncertainty + 1 / 60

            # The next stroke is observed afresh, so each possible reset can
            # choose its own release window. A changing body attachment needs
            # a whole orbit before trusting that adaptive forecast; until then
            # retain the stronger common-window estimate and current Good.
            adaptive_route = (
                span >= 2 * math.pi / 1.6
                or np.ptp(points[:, 1] - points[:, 5]) <= 10
                or (not short and np.hypot(attachment[1], attachment[2]) <= 5)
            )

            def route_score(delay):
                nominal, flight_now = landing(delay)
                # Next BeginMovement starts close to actual contact. The small
                # scheduling offset is only a routing estimate, never a guard.
                spawn = age + lead_center + delay + flight_now + 1 / 60 + 1 / 30
                contacts = spawn + release_offsets[None, :, :, None] + flights + 1 / 60
                # Contact and the next movement reset are separate events.
                # Carry the current release/fall envelope into that reset;
                # otherwise a nominally easy next stroke can actually be fast.
                contact_times = (
                    contacts[..., None, None]
                    + np.array([-1 / 60, 0.0, 1 / 60])[:, None]
                    + np.array([-spawn_uncertainty, 0.0, spawn_uncertainty])
                )
                future_target = (
                    tower_x[0]
                    + nominal
                    + gain
                    * (
                        tower_x[1] * np.sin(math.pi / 2 * contact_times)
                        + tower_x[2] * np.cos(math.pi / 2 * contact_times)
                    )
                )
                errors = np.max(
                    abs(future_landing[..., None, None] - future_target),
                    axis=(2, 3, 4) if adaptive_route else (2, 3, 4, 5),
                )
                # The body anchor is independent of short-arc clock fitting.
                # Keep its observed error outside that reserve so an uncertain
                # future estimate cannot displace a verified current Good.
                reserve = np.maximum(next_reserve, next_fit)[None, :]
                budgets = errors + (reserve[..., None] if adaptive_route else reserve) + 4.0 + offset_error
                good = budgets <= next_margin
                earliest = np.where(
                    np.any(good, axis=1), taus[np.argmax(good, axis=1)], 9.0 + np.min(budgets, axis=1) / 30
                )
                return delay + flight_now + float(np.max(earliest)), float(np.max(np.min(budgets, axis=1)))

            ranked = [(*route_score(delay), delay, error) for delay, error in safe]
            best_score = min(item[0] for item in ranked)
            current_score, current_bound, current_delay, current_error = min(
                (item for item in ranked if item[0] <= best_score + 0.15),
                key=lambda item: (abs(landing(item[2])[0] - desired_offset), item[3]),
            )
            # A short current Good must also support the next reset. If it
            # does, use that stroke without waiting for a full arc; otherwise
            # collect the longer history before making any far handoff plan.
            if short and current_bound > next_margin:
                return False
            if short:
                safe = [(current_delay, current_error)]
            elif self.handoff_target_at is not None:
                if now + self.max_delay < self.handoff_target_at:
                    return False
                safe = [min(safe, key=lambda item: abs(now + item[0] - self.handoff_target_at))]
                self.handoff_target_at = None
                self.handoff_window = None
            elif not self.handoff_waited:
                horizon = 2 * math.pi / 1.6
                future_delays = list(np.arange(self.max_delay, horizon, 0.125))
                for left_delay, right_delay in zip(
                    [self.max_delay, *future_delays], [*future_delays, horizon], strict=True
                ):
                    if landing(left_delay)[0] * landing(right_delay)[0] < 0:
                        for _ in range(15):
                            middle_delay = (left_delay + right_delay) / 2
                            if landing(left_delay)[0] * landing(middle_delay)[0] <= 0:
                                right_delay = middle_delay
                            else:
                                left_delay = middle_delay
                        future_delays.append((left_delay + right_delay) / 2)
                alternatives = []
                for delay in future_delays:
                    error = candidate_error(delay, forecast=True)
                    # This forecast only schedules a fresh observation. Its
                    # complete current contact guard must pass again before
                    # input; adding a second routing reserve can reject the
                    # only handoff that avoids a long opposite-phase wait.
                    if error <= landing_margin:
                        score, next_bound = route_score(delay)
                        alternatives.append((score, delay, error, next_bound))
                if alternatives:
                    # Unknown future faces can leave every route just outside
                    # the conservative next-piece margin. Prefer a materially
                    # better next contact instead of assigning both such routes
                    # a cheap arbitrary wait and taking an opposite-phase handoff.
                    best = (
                        min(alternatives, key=lambda item: (item[3], item[0]))
                        if current_bound > next_margin
                        else min(alternatives)
                    )
                    self.route_debug = {
                        "current": (current_score, current_delay, current_error, current_bound),
                        "alternative": best,
                    }
                    faster = best[0] + 0.75 < current_score and best[3] <= next_margin
                    better_contact = (
                        current_bound > next_margin
                        and (adaptive_route or best[3] <= next_margin)
                        and best[3] + max(3.0, 2 * (error_x + error_tower)) < current_bound
                    )
                    if faster or better_contact:
                        self.handoff_target_at = now + best[1]
                        # Refit within this original contact neighborhood;
                        # repeated observations must not roll the wait onward.
                        self.handoff_window = (self.handoff_target_at - 0.125, self.handoff_target_at + 0.125)
                        self.handoff_waited = True
                        logger.debug(
                            "Jenga handoff wait %.3fs next_bound=%.2f current_cost=%.2f route_cost=%.2f",
                            best[1],
                            best[3],
                            current_score,
                            best[0],
                        )
                        return False
                safe = [(current_delay, current_error)]
            else:
                safe = [(current_delay, current_error)]

        self.release_delay, landing_error = min(
            safe,
            key=(lambda item: (item[0], item[1]))
            if greedy
            else (lambda item: (abs(landing(item[0])[0] - desired_offset), item[1])),
        )
        release_time = age + lead_center + self.release_delay
        duration = landing(self.release_delay)[1]
        velocity = self._velocity(bird_x, 1.6, release_time)
        target_velocity = self._velocity(tower_x, math.pi / 2, release_time + duration + contact_clock_step)
        self.prediction = {
            "scale": scale,
            "flight_time": landing(self.release_delay)[1],
            "fit_errors": (error_x, error_y, error_tower),
            "coefficients": [bird_x.tolist(), bird_y.tolist(), tower_x.tolist()],
            "at": observed_at,
            "now": now,
            "held": carried.__dict__,
            "target": tower.__dict__,
            "delay": self.release_delay,
            "relative_velocity": velocity - target_velocity,
            "landing_error": landing_error,
            "timing_uncertainty": timing_uncertainty,
            "flight_uncertainty": flight_uncertainty,
            "flight_interval": (-flight_early, flight_uncertainty),
            "landing_margin": landing_margin,
            "short_observation": short,
            "nominal_offset": landing(self.release_delay)[0],
            "desired_offset": desired_offset,
            "handoff_direction": self.handoff_direction,
            "target_velocity": target_velocity,
            "greedy": greedy,
            "measured_contact": measured_contact,
            "contact_top": contact_top,
        }
        return True


class AutoJengaTask(BaseGameTask, TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = self.tr("自动祖赞卡之梯")
        self.description = self.tr("检测到祖赞卡之梯界面自动完成。")
        self.icon = Icons.Trigger
        self.trigger_interval = 0.1
        self.default_config.update({"_input_lead_ms": 45, "_fall_time_ms": 500, "_drift_time_ms": 340})
        self._jenga_detector = JengaDetector()
        self._key_lock = threading.RLock()
        self._held_key = None
        self._held_interaction = None
        self._destroyed = False
        self._session_generation = 0

    def _can_input(self, hwnd):
        return (
            self.enabled
            and not self._destroyed
            and not self.paused
            and not self.executor.paused
            and not self.executor.exit_event.is_set()
            and self.executor.interaction is not None
            and hwnd
            and win32gui.GetForegroundWindow() == hwnd
        )

    def _release_key(self):
        with self._key_lock:
            if self._held_key is not None:
                interaction = self._held_interaction or self.executor.interaction
                try:
                    interaction.send_key_up(self._held_key)
                finally:
                    self._held_key = None
                    self._held_interaction = None

    def _capture_context(self):
        # pause_start changes even when next_frame blocks through a complete
        # pause/resume. A replacement capture method starts another session too.
        return (
            id(getattr(self.executor, "method", None)),
            id(self.executor.interaction),
            getattr(self.executor, "pause_start", None),
            self._session_generation,
        )

    @staticmethod
    def _new_player():
        return JengaPlayer(max_delay=0.4, greedy_high_sway=True, greedy_release=True)

    def _drop(self, hwnd, deadline=None, *, capture_context=None):
        # Trigger tasks do not include the daily account-override mixin.
        key = self.key_manager.resolve_key("f", "common")
        try:
            with self._key_lock:
                if not self._can_input(hwnd):
                    return False
                if capture_context is not None and self._capture_context() != capture_context:
                    return False
                # sleep() and lock acquisition can overshoot on a busy machine.
                # Check at dispatch, after both waits, before holding any key.
                if deadline is not None and time.perf_counter() > deadline:
                    return False
                self._held_key = key
                self._held_interaction = self.executor.interaction
                if self._held_interaction.send_key_down(key, activate=False) is False:
                    return False
            # This short physical pulse must end even if the task is paused meanwhile.
            time.sleep(0.04)
            return True
        finally:
            self._release_key()

    def _wait_placement(self, hwnd):
        """Confirm the rendered result before accepting another piece."""
        votes = {}
        last_pixels = None
        last_observed = -math.inf
        capture_context = self._capture_context()
        for frame in self.loop(time_out=2.5, raise_if_time_out=False):
            if (
                not self._can_input(hwnd)
                or self._capture_context() != capture_context
                or frame is None
                or not self._jenga_detector.is_active(frame)
            ):
                return None
            captured = time.perf_counter()
            observed_at = getattr(getattr(self.executor, "method", None), "frame_timestamp", None)
            if isinstance(observed_at, (int, float)):
                if (
                    not math.isfinite(observed_at)
                    or not -0.01 <= captured - observed_at <= 0.18
                    or observed_at <= last_observed
                ):
                    continue
                last_observed = observed_at
            pixels = frame[::8, ::8, :3]
            if last_pixels is not None and np.array_equal(pixels, last_pixels):
                continue
            last_pixels = pixels.copy()
            result = self._jenga_detector.placement_result(frame)
            if result:
                votes[result] = votes.get(result, 0) + 1
                if votes[result] >= 2:
                    return result
        return None

    def run(self):
        self._jenga_detector.reset_tracking(clear_learned=True)
        session_started = False
        try:
            frame = self.next_frame()
            if not self._jenga_detector.is_active(frame):
                return
            hwnd = self.get_game_hwnd()
            if not self._can_input(hwnd):
                return
            # Keep legacy config keys intact; native gravity determines flight
            # time. Every future candidate retains the full contact budget.
            player = self._new_player()
            input_lead = max(0, min(0.15, float(self.config.get("_input_lead_ms", 45)) / 1000))
            capture_context = self._capture_context()
            frame_shape = frame.shape
            self.info_set("current task", self.tr("正在对准图腾"))
            session_started = True
            last_pixels = None
            changed_at = time.perf_counter()
            last_observed = -math.inf
            missing_since = None
            for _ in self.loop(time_out=math.inf, yield_frame=False, raise_if_time_out=False):
                if (
                    not self.enabled
                    or self._destroyed
                    or self.executor.exit_event.is_set()
                    or not self._can_input(hwnd)
                    or self._capture_context() != capture_context
                ):
                    break
                started = time.perf_counter()
                frame = self.next_frame()
                captured = time.perf_counter()
                if (
                    not self._can_input(hwnd)
                    or self._capture_context() != capture_context
                    or frame is None
                    or frame.shape != frame_shape
                    or captured - started > 1.0
                    or not self._jenga_detector.is_active(frame)
                ):
                    break  # Stay enabled; the next trigger reacquires the current scene/window.
                observed_at = getattr(getattr(self.executor, "method", None), "frame_timestamp", None)
                if not isinstance(observed_at, (int, float)):
                    observed_at = (started + captured) / 2
                if (
                    not math.isfinite(observed_at)
                    or not -0.01 <= captured - observed_at <= 0.18
                    or observed_at <= last_observed
                ):
                    if captured - changed_at > 0.3:
                        break
                    # Never relabel a stale WGC image as a fresh observation.
                    continue
                last_observed = observed_at
                pixels = frame[::8, ::8, :3]
                if last_pixels is not None and np.array_equal(pixels, last_pixels):
                    if captured - changed_at > 0.3:
                        break
                    continue
                changed_at = captured
                last_pixels = pixels.copy()
                observation = self._jenga_detector.detect(frame)
                now = time.perf_counter()
                if observation is None:
                    if missing_since is None:
                        missing_since = now
                    elif now - missing_since >= 2 * math.pi / 1.6:
                        # An old kind/height constraint must not hide the real
                        # receiver forever after an interrupted or missed drop.
                        self._jenga_detector.reset_tracking()
                        player = self._new_player()
                        missing_since = now
                        logger.info("Jenga tracking lost; reacquiring current pieces")
                        continue
                else:
                    missing_since = None
                ready = player.update(observation, observed_at, now, input_lead)
                if player.horizontal_geometry is not None:
                    self._jenga_detector.pixels_per_unit = player.horizontal_geometry[1] / 1.25
                if not ready:
                    continue
                release_at = now + player.release_delay
                deadline = release_at + 0.015
                if time.perf_counter() > deadline:
                    continue  # A prediction that missed its deadline must not press late.
                delay = max(0, release_at - time.perf_counter())
                if delay:
                    time.sleep(delay)
                if self._drop(hwnd, deadline=deadline, capture_context=capture_context):
                    player.mark_released(time.perf_counter(), observation.carried)
                    logger.debug(
                        "Jenga release carried_x=%.1f tower_x=%.1f frame_age=%.3f",
                        observation.carried.x,
                        observation.tower.x,
                        now - observed_at,
                    )
                    result = self._wait_placement(hwnd)
                    logger.info("Jenga placement result=%s", result or "unknown")
                    confirmed = result in ("perfect", "good")
                    self._jenga_detector.learn(frame, observation.carried, placement_confirmed=confirmed)
                    if confirmed:
                        player.confirm_placement()
                    else:
                        # Judgment polling consumed the disappearance frames.
                        # Rebuild both motion and receiver identity instead of
                        # keeping an unresolved release latch or a false top.
                        player = self._new_player()
                    missing_since = None
                    last_pixels = None
        except CaptureException as error:
            logger.warning("Jenga capture interrupted; waiting for the next trigger: %s", error)
        finally:
            self._release_key()
            self._jenga_detector.reset_tracking(clear_learned=True)
            if session_started:
                self.info_set("current task", self.tr("祖赞卡之梯辅助已停止"))

    def disable(self):
        # Disable before taking the lock, so a concurrently prepared pulse cannot start.
        self._session_generation += 1
        super().disable()
        self._release_key()

    def on_destroy(self):
        self._destroyed = True
        self._release_key()
        super().on_destroy()
