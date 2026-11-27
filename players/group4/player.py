from core.action import Action
from core.constants import DAYS_PER_SEASON, OCEAN_SIZE
from core.depth import Depth
from core.player import Player
from core.seasons import Season
from core.snapshot import JellyfishSnapshot


class Player4(Player):
    """Group 4's jellyfish.

    Called once per living jellyfish per day. Read what the jellyfish senses
    from `snapshot`, update `snapshot.memory` in place if you want to
    remember something, and return an Action choosing today's depth.

    Do not keep per-jellyfish state on `self`: memory is the only memory.
    """

    # Header stored in the jellyfish's bytearray.  Positions are quantized to
    # whole kilometres and represented as signed 16-bit values.  The ocean is
    # a 1000 km torus, so this is more than enough precision for a first map.
    _X = 0
    _Y = 2
    _LAST_DX = 4  # signed velocity, tenths of a km/day
    _LAST_DY = 6
    _BIRTH_DAY = 8
    _BIRTH_YEAR = 9
    _MAGIC = 10
    _COUNTERS = 11
    _PREV_FLAGS = 14
    _SEARCH_STATE = 15
    _SAMPLE_COUNTS = 17
    _SAMPLES = 20
    _CENTERS = 56
    _CENTER_MASK = 68
    _BORN_BREEDING = 69
    _VELOCITIES = 70
    _MAPPED_MEMORY = _VELOCITIES + 4 * len(Depth)
    _SEARCH_DEPTHS = (Depth.SURFACE, Depth.SHALLOW, Depth.MEDIUM)

    _COORDINATE_BYTES = 3
    _BIRTHPLACE_X = _MAPPED_MEMORY
    _BIRTHPLACE_Y = _BIRTHPLACE_X + _COORDINATE_BYTES
    _BIRTHPLACE_MEMORY = _BIRTHPLACE_Y + _COORDINATE_BYTES
    _UNITS_PER_KM = 10_000  # 0.1 m per unit
    _WORLD_UNITS = int(OCEAN_SIZE * _UNITS_PER_KM)

    @staticmethod
    def _read_i16(memory: bytearray, offset: int) -> int:
        value = memory[offset] | (memory[offset + 1] << 8)
        return value - 65536 if value >= 32768 else value

    @staticmethod
    def _write_i16(memory: bytearray, offset: int, value: int) -> None:
        value %= 65536
        memory[offset] = value & 0xFF
        memory[offset + 1] = (value >> 8) & 0xFF

    @staticmethod
    def _wrap(value: int) -> int:
        """Keep an estimated coordinate in the 1000 km torus."""
        value %= 1000
        return value - 1000 if value >= 500 else value

    @classmethod
    def _record_boundary(cls, memory: bytearray, zone: int, x: int, y: int) -> None:
        count = memory[cls._SAMPLE_COUNTS + zone]
        if count >= 3:
            return
        offset = cls._SAMPLES + (zone * 3 + count) * 4
        cls._write_i16(memory, offset, x)
        cls._write_i16(memory, offset + 2, y)
        memory[cls._SAMPLE_COUNTS + zone] = count + 1

        if count != 2:
            return
        p = []
        for i in range(3):
            base = cls._SAMPLES + (zone * 3 + i) * 4
            p.append((cls._read_i16(memory, base), cls._read_i16(memory, base + 2)))
        (x1, y1), (x2, y2), (x3, y3) = p
        d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        if d == 0:
            return
        ux = (
            (x1 * x1 + y1 * y1) * (y2 - y3)
            + (x2 * x2 + y2 * y2) * (y3 - y1)
            + (x3 * x3 + y3 * y3) * (y1 - y2)
        ) / d
        uy = (
            (x1 * x1 + y1 * y1) * (x3 - x2)
            + (x2 * x2 + y2 * y2) * (x1 - x3)
            + (x3 * x3 + y3 * y3) * (x2 - x1)
        ) / d
        center = cls._CENTERS + zone * 4
        cls._write_i16(memory, center, round(cls._wrap(round(ux))))
        cls._write_i16(memory, center + 2, round(cls._wrap(round(uy))))
        memory[cls._CENTER_MASK] |= 1 << zone

    @classmethod
    def _best_route_depth(
        cls, memory: bytearray, current: Depth, tx: int, ty: int
    ) -> Depth:
        best = current
        best_score = float("-inf")
        for value in Depth:
            base = cls._VELOCITIES + value.value * 4
            dx = cls._read_i16(memory, base) / 10
            dy = cls._read_i16(memory, base + 2) / 10
            if dx == 0 and dy == 0:
                continue
            score = dx * tx + dy * ty
            if score > best_score:
                best_score, best = score, value
        return best

    @classmethod
    def _best_stay_depth(
        cls, memory: bytearray, current: Depth, tx: int, ty: int
    ) -> Depth:
        """Choose the known current that leaves us closest to a zone center."""
        best = current
        best_distance = float("inf")
        for value in Depth:
            base = cls._VELOCITIES + value.value * 4
            dx = cls._read_i16(memory, base) / 10
            dy = cls._read_i16(memory, base + 2) / 10
            if dx == 0 and dy == 0:
                continue
            distance = (tx - dx) ** 2 + (ty - dy) ** 2
            if distance < best_distance:
                best_distance, best = distance, value
        return best

    @classmethod
    def _route_depth(
        cls, memory: bytearray, current: Depth, x: int, y: int, zone: int
    ) -> Depth | None:
        """Route to a mapped center, including reverse/backtracking currents."""
        if not (memory[cls._CENTER_MASK] & (1 << zone)):
            return None
        center = cls._CENTERS + zone * 4
        cx = cls._read_i16(memory, center)
        cy = cls._read_i16(memory, center + 2)
        tx = ((cx - x + 500) % 1000) - 500
        ty = ((cy - y + 500) % 1000) - 500
        if tx * tx + ty * ty < 80 * 80:
            return cls._best_stay_depth(memory, current, tx, ty)
        return cls._best_route_depth(memory, current, tx, ty)

    @classmethod
    def _center_delta(
        cls, memory: bytearray, x: int, y: int, zone: int
    ) -> tuple[int, int] | None:
        if not (memory[cls._CENTER_MASK] & (1 << zone)):
            return None
        center = cls._CENTERS + zone * 4
        cx = cls._read_i16(memory, center)
        cy = cls._read_i16(memory, center + 2)
        return ((cx - x + 500) % 1000 - 500, (cy - y + 500) % 1000 - 500)

    def _known_velocity(self, memory: bytearray, depth: Depth) -> tuple[float, float]:
        offset = self._VELOCITIES + 4 * depth
        return (
            self._read_i16(memory, offset) / 10,
            self._read_i16(memory, offset + 2) / 10,
        )

    def _explore_depth(
        self, snapshot: JellyfishSnapshot, *, restart: bool = False
    ) -> Depth:
        memory = snapshot.memory
        mapped = len(memory) >= self._MAPPED_MEMORY
        slot = self._SEARCH_STATE if mapped else 0
        if restart or snapshot.day_of_year in (1, 2 * DAYS_PER_SEASON + 1):
            memory[slot] = 0

        state = memory[slot]
        previous = Depth((state >> 4) - 1) if state else None
        if previous is not None and state & 0x0F:
            memory[slot] -= 1
            return previous

        if mapped and previous is None:
            for depth in Depth:
                if self._known_velocity(memory, depth) == (0, 0):
                    return depth

        options = [depth for depth in self._SEARCH_DEPTHS if depth != previous]
        if mapped and previous is not None:
            vx, vy = self._known_velocity(memory, previous)
            different_headings = []
            for depth in options:
                dx, dy = self._known_velocity(memory, depth)
                if abs(vx * dx + vy * dy) < 0.9 * previous.speed * depth.speed:
                    different_headings.append(depth)
            options = different_headings or options

        depth = self.rng.choice(options)
        # Store depth plus one and days left after today's move in one byte.
        memory[slot] = ((depth.value + 1) << 4) | self.rng.randint(3, 7)
        return depth

    def _track_birthplace_axis(
        self, memory: bytearray, offset: int, velocity: float, first_turn: bool
    ) -> int:
        end = offset + self._COORDINATE_BYTES
        position = 0
        if not first_turn:
            position = int.from_bytes(memory[offset:end], "little")
            position = (position + round(velocity * self._UNITS_PER_KM)) % (
                self._WORLD_UNITS
            )
        memory[offset:end] = position.to_bytes(self._COORDINATE_BYTES, "little")
        half_world = self._WORLD_UNITS // 2
        return (half_world - position) % self._WORLD_UNITS - half_world

    def _birthplace_delta(self, snapshot: JellyfishSnapshot) -> tuple[int, int]:
        # Founders first act on their birth day. Newborns first act the next
        # day, from their parent's position before the parent moved.
        founder = snapshot.birth_year == 1 and snapshot.birth_day_of_year == 1
        first_turn = snapshot.age_days == (0 if founder else 1)
        vx, vy = snapshot.velocity
        return (
            self._track_birthplace_axis(
                snapshot.memory, self._BIRTHPLACE_X, vx, first_turn
            ),
            self._track_birthplace_axis(
                snapshot.memory, self._BIRTHPLACE_Y, vy, first_turn
            ),
        )

    @classmethod
    def _mapping_depth(
        cls,
        memory: bytearray,
        current: Depth,
        x: int,
        y: int,
        zone: int,
        inside: bool,
    ) -> Depth:
        """Collect three crossings without wandering away from the zone."""
        count = memory[cls._SAMPLE_COUNTS + zone]
        if inside:
            # Leave promptly so the next crossing is an exit point.
            return Depth.SURFACE
        if count:
            # After an exit, deliberately backtrack toward the latest border
            # point. The resulting re-entry supplies a third, distinct sample.
            base = cls._SAMPLES + (zone * 3 + count - 1) * 4
            bx = cls._read_i16(memory, base)
            by = cls._read_i16(memory, base + 2)
            tx = ((bx - x + 500) % 1000) - 500
            ty = ((by - y + 500) % 1000) - 500
            return cls._best_route_depth(memory, current, tx, ty)
        return Depth.SHALLOW

    def act(self, snapshot: JellyfishSnapshot) -> Action:
        memory = snapshot.memory

        if len(memory) < self._MAPPED_MEMORY:
            if snapshot.in_predator_zone:
                memory[0] = 0
                return Action(Depth.SURFACE, eat=False, reproduce=False)
            if snapshot.season == Season.SUMMER and snapshot.in_feeding_zone:
                memory[0] = 0
                return Action(Depth.DEEP)
            if snapshot.season == Season.WINTER and snapshot.in_breeding_zone:
                memory[0] = 0
                return Action(Depth.DEEP)
            return Action(self._explore_depth(snapshot))

        birthplace = None
        if len(memory) >= self._BIRTHPLACE_MEMORY:
            birthplace = self._birthplace_delta(snapshot)

        # A copied memory block belongs to a new jellyfish after birth.  The
        # parent has written its current estimate into _X/_Y before making the
        # child, so the child can use that position as its new local origin.
        birth_day = snapshot.birth_day_of_year
        birth_year = snapshot.birth_year
        just_initialized = False
        if (
            memory[self._MAGIC] != 0xA4
            or memory[self._BIRTH_DAY] != birth_day & 0xFF
            or memory[self._BIRTH_YEAR] != birth_year & 0xFF
        ):
            just_initialized = True
            # The inherited map is relative to the parent's birth origin.
            # Rebase known centers around the child's birth position before
            # resetting the child's own local coordinates.
            parent_x = self._read_i16(memory, self._X)
            parent_y = self._read_i16(memory, self._Y)
            inherited_mask = memory[self._CENTER_MASK]
            inherited_centers = [
                (
                    self._read_i16(memory, self._CENTERS + zone * 4),
                    self._read_i16(memory, self._CENTERS + zone * 4 + 2),
                )
                for zone in range(3)
            ]
            # The inherited estimate is the child's birth location.  Reset
            # local coordinates and rebase the inherited center map.
            self._write_i16(memory, self._X, 0)
            self._write_i16(memory, self._Y, 0)
            for zone, (cx, cy) in enumerate(inherited_centers):
                if inherited_mask & (1 << zone):
                    center = self._CENTERS + zone * 4
                    self._write_i16(memory, center, self._wrap(cx - parent_x))
                    self._write_i16(memory, center + 2, self._wrap(cy - parent_y))
            memory[self._CENTER_MASK] = inherited_mask
            memory[self._BIRTH_DAY] = birth_day & 0xFF
            memory[self._BIRTH_YEAR] = birth_year & 0xFF
            self._write_i16(memory, self._LAST_DX, round(snapshot.velocity[0] * 10))
            self._write_i16(memory, self._LAST_DY, round(snapshot.velocity[1] * 10))
            memory[self._MAGIC] = 0xA4
            for i in range(self._PREV_FLAGS, self._CENTERS):
                memory[i] = 0
        else:
            # The previous action's current carried the jellyfish between
            # yesterday's and today's observations.
            x = self._read_i16(memory, self._X)
            y = self._read_i16(memory, self._Y)
            dx = self._read_i16(memory, self._LAST_DX) / 10
            dy = self._read_i16(memory, self._LAST_DY) / 10
            self._write_i16(memory, self._X, self._wrap(x + round(dx)))
            self._write_i16(memory, self._Y, self._wrap(y + round(dy)))

        # Learn the vector for the current depth.  Later, once a destination
        # is mapped, these vectors let us select among currents.
        velocity_base = self._VELOCITIES + int(snapshot.depth) * 4
        self._write_i16(memory, velocity_base, round(snapshot.velocity[0] * 10))
        self._write_i16(memory, velocity_base + 2, round(snapshot.velocity[1] * 10))

        flags = (
            int(snapshot.in_feeding_zone)
            | (int(snapshot.in_breeding_zone) << 1)
            | (int(snapshot.in_predator_zone) << 2)
        )
        x = self._read_i16(memory, self._X)
        y = self._read_i16(memory, self._Y)
        old_flags = memory[self._PREV_FLAGS]
        if not just_initialized:
            for zone in range(3):
                bit = 1 << zone
                if (flags & bit) != (old_flags & bit):
                    self._record_boundary(memory, zone, x, y)
        memory[self._PREV_FLAGS] = flags

        if snapshot.in_feeding_zone:
            memory[self._COUNTERS] = min(255, memory[self._COUNTERS] + 1)
        if snapshot.in_breeding_zone:
            memory[self._COUNTERS + 1] = min(255, memory[self._COUNTERS + 1] + 1)
        if snapshot.in_predator_zone:
            memory[self._COUNTERS + 2] = min(255, memory[self._COUNTERS + 2] + 1)

        # Choose the slow current when a zone is useful, and a faster one when
        # searching.  Predator exposure takes priority over reproduction.
        if snapshot.in_predator_zone:
            depth = Depth.SURFACE
            eat = False
            reproduce = False
        elif (
            snapshot.season in (Season.SPRING, Season.SUMMER)
            and snapshot.in_feeding_zone
        ):
            depth = self._route_depth(memory, snapshot.depth, x, y, 0) or Depth.DEEP
            eat = snapshot.season == Season.SUMMER
            reproduce = False
        elif (
            snapshot.season in (Season.FALL, Season.WINTER)
            and snapshot.in_breeding_zone
        ):
            # Once breeding is found before winter, stop navigating. The
            # slowest current gives the jellyfish the best chance of staying
            # inside until the reproductive season begins.
            depth = Depth.DEEP
            eat = False
            reproduce = snapshot.season == Season.WINTER
        else:
            # Prepare during the preceding season: reach feeding before
            # summer begins, and reach breeding before winter begins.
            target_zone = 0 if snapshot.season in (Season.SPRING, Season.SUMMER) else 1
            depth = self._route_depth(memory, snapshot.depth, x, y, target_zone)
            if depth is None:
                # Initial jellyfish are born inside breeding, so their birth
                # origin is a useful fallback target before its center is
                # mapped. Later generations use normal probing.
                if target_zone == 1 and birth_day == 1 and birth_year == 1:
                    depth = self._best_route_depth(memory, snapshot.depth, -x, -y)
                else:
                    depth = self._explore_depth(snapshot, restart=flags != old_flags)
            if (
                target_zone == 1
                and birthplace is not None
                and snapshot.reproduction_capacity
            ):
                # The birthplace is inside breeding and outside predators.
                depth = self._best_route_depth(memory, snapshot.depth, *birthplace)
            eat = snapshot.season == Season.SUMMER and snapshot.in_feeding_zone
            reproduce = snapshot.season == Season.WINTER and snapshot.in_breeding_zone

        # Store today's chosen current.  Reproduction copies this value too,
        # which gives the child a sensible initial movement convention.
        # Save the actual velocity, not just the depth: different depths can
        # have different headings, and the next call only exposes today's
        # heading.
        # Use the learned vector for the selected depth when available;
        # otherwise use today's observed vector as the initial estimate.
        selected_base = self._VELOCITIES + int(depth) * 4
        next_dx = self._read_i16(memory, selected_base)
        next_dy = self._read_i16(memory, selected_base + 2)
        if next_dx == 0 and next_dy == 0:
            next_dx = round(snapshot.velocity[0] * 10)
            next_dy = round(snapshot.velocity[1] * 10)
        self._write_i16(memory, self._LAST_DX, next_dx)
        self._write_i16(memory, self._LAST_DY, next_dy)
        return Action(depth=depth, eat=eat, reproduce=reproduce)
