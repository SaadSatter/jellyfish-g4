from core.action import Action
from core.constants import DAYS_PER_SEASON, OCEAN_SIZE
from core.depth import Depth
from core.player import Player
from core.seasons import Season
from core.snapshot import JellyfishSnapshot


class Player4(Player):
    """Learn currents and zone locations, then steer between seasonal targets."""

    _FEEDING, _BREEDING, _PREDATOR = range(3)
    _OCEAN_KM = int(OCEAN_SIZE)
    _SEARCH_DEPTHS = (Depth.SURFACE, Depth.SHALLOW, Depth.MEDIUM)
    _HOLD_RADIUS_KM = 80

    # Map positions use km. Stored currents use tenths of a km per day.
    _X = 0
    _Y = 2
    _LAST_DX = 4
    _LAST_DY = 6
    _BIRTH_DAY = 8
    _BIRTH_YEAR = 9
    _MAP_MARKER = 10
    _MAP_INITIALIZED = 0xA4
    _PREV_FLAGS = 14
    _SEARCH_STATE = 15
    _SAMPLE_COUNTS = 17
    _SAMPLES = 20
    _CENTERS = 56
    _CENTER_MASK = 68
    _VELOCITIES = 70
    _MAPPED_MEMORY = _VELOCITIES + 4 * len(Depth)

    # The larger layout tracks position relative to birth in 0.1 m units.
    _COORDINATE_BYTES = 3
    _BIRTHPLACE_X = _MAPPED_MEMORY
    _BIRTHPLACE_Y = _BIRTHPLACE_X + _COORDINATE_BYTES
    _BIRTHPLACE_MEMORY = _BIRTHPLACE_Y + _COORDINATE_BYTES
    _UNITS_PER_KM = 10_000
    _WORLD_UNITS = _OCEAN_KM * _UNITS_PER_KM

    def act(self, snapshot: JellyfishSnapshot) -> Action:
        if len(snapshot.memory) < self._MAPPED_MEMORY:
            return self._act_without_map(snapshot)

        birthplace = None
        if len(snapshot.memory) >= self._BIRTHPLACE_MEMORY:
            birthplace = self._birthplace_delta(snapshot)

        x, y, zone_changed = self._update_map(snapshot)
        action = self._choose_action(snapshot, x, y, zone_changed, birthplace)
        self._remember_drift(snapshot, action.depth)
        return action

    def _act_without_map(self, snapshot: JellyfishSnapshot) -> Action:
        if snapshot.in_predator_zone:
            snapshot.memory[0] = 0
            return Action(Depth.SURFACE, eat=False, reproduce=False)

        feeding = snapshot.season == Season.SUMMER and snapshot.in_feeding_zone
        breeding = snapshot.season == Season.WINTER and snapshot.in_breeding_zone
        if feeding or breeding:
            snapshot.memory[0] = 0
            return Action(Depth.DEEP)
        return Action(self._explore_depth(snapshot))

    def _choose_action(
        self,
        snapshot: JellyfishSnapshot,
        x: int,
        y: int,
        zone_changed: bool,
        birthplace: tuple[int, int] | None,
    ) -> Action:
        memory = snapshot.memory
        if snapshot.in_predator_zone:
            return Action(Depth.SURFACE, eat=False, reproduce=False)

        heading_to_food = snapshot.season in (Season.SPRING, Season.SUMMER)
        if heading_to_food and snapshot.in_feeding_zone:
            depth = self._steer_to_zone(memory, snapshot.depth, x, y, self._FEEDING)
            # The feeding policy stays deep when it would otherwise surface.
            if depth is None or depth == Depth.SURFACE:
                depth = Depth.DEEP
            return Action(depth, eat=snapshot.season == Season.SUMMER, reproduce=False)

        if not heading_to_food and snapshot.in_breeding_zone:
            return Action(
                Depth.DEEP, eat=False, reproduce=snapshot.season == Season.WINTER
            )

        target = self._FEEDING if heading_to_food else self._BREEDING
        depth = self._steer_to_zone(memory, snapshot.depth, x, y, target)
        if depth is None:
            if target == self._BREEDING and self._is_founder(snapshot):
                depth = self._best_route_depth(memory, snapshot.depth, -x, -y)
            else:
                depth = self._explore_depth(snapshot, restart=zone_changed)

        # Apply the birthplace override after updating the search state.
        if (
            target == self._BREEDING
            and birthplace is not None
            and snapshot.reproduction_capacity
        ):
            depth = self._best_route_depth(memory, snapshot.depth, *birthplace)
        return Action(depth, eat=False, reproduce=False)

    @staticmethod
    def _is_founder(snapshot: JellyfishSnapshot) -> bool:
        return snapshot.birth_year == 1 and snapshot.birth_day_of_year == 1

    def _update_map(self, snapshot: JellyfishSnapshot) -> tuple[int, int, bool]:
        memory = snapshot.memory
        birth_day = snapshot.birth_day_of_year & 0xFF
        birth_year = snapshot.birth_year & 0xFF
        new_jellyfish = (
            memory[self._MAP_MARKER] != self._MAP_INITIALIZED
            or memory[self._BIRTH_DAY] != birth_day
            or memory[self._BIRTH_YEAR] != birth_year
        )
        if new_jellyfish:
            self._rebase_map(memory)
            memory[self._BIRTH_DAY] = birth_day
            memory[self._BIRTH_YEAR] = birth_year
            memory[self._MAP_MARKER] = self._MAP_INITIALIZED
            x, y = 0, 0
        else:
            dx = round(self._read_i16(memory, self._LAST_DX) / 10)
            dy = round(self._read_i16(memory, self._LAST_DY) / 10)
            x = self._wrap_delta(self._read_i16(memory, self._X) + dx)
            y = self._wrap_delta(self._read_i16(memory, self._Y) + dy)
        self._write_i16(memory, self._X, x)
        self._write_i16(memory, self._Y, y)

        offset = self._VELOCITIES + 4 * snapshot.depth
        vx, vy = snapshot.velocity
        self._write_i16(memory, offset, round(vx * 10))
        self._write_i16(memory, offset + 2, round(vy * 10))

        flags = (
            (int(snapshot.in_feeding_zone) << self._FEEDING)
            | (int(snapshot.in_breeding_zone) << self._BREEDING)
            | (int(snapshot.in_predator_zone) << self._PREDATOR)
        )
        changed = flags ^ memory[self._PREV_FLAGS]
        if not new_jellyfish:
            for zone in (self._FEEDING, self._BREEDING, self._PREDATOR):
                if changed & (1 << zone):
                    self._record_crossing(memory, zone, x, y)
        memory[self._PREV_FLAGS] = flags
        return x, y, bool(changed)

    def _rebase_map(self, memory: bytearray) -> None:
        # The parent saved its position before movement. This is the child's origin.
        parent_x = self._read_i16(memory, self._X)
        parent_y = self._read_i16(memory, self._Y)
        for zone in (self._FEEDING, self._BREEDING, self._PREDATOR):
            if memory[self._CENTER_MASK] & (1 << zone):
                offset = self._CENTERS + zone * 4
                x = self._read_i16(memory, offset)
                y = self._read_i16(memory, offset + 2)
                self._write_i16(memory, offset, self._wrap_delta(x - parent_x))
                self._write_i16(memory, offset + 2, self._wrap_delta(y - parent_y))
        memory[self._PREV_FLAGS : self._CENTERS] = bytes(
            self._CENTERS - self._PREV_FLAGS
        )

    def _record_crossing(self, memory: bytearray, zone: int, x: int, y: int) -> None:
        count_offset = self._SAMPLE_COUNTS + zone
        count = memory[count_offset]
        if count >= 3:
            return

        first_sample = self._SAMPLES + zone * 12
        offset = first_sample + count * 4
        self._write_i16(memory, offset, x)
        self._write_i16(memory, offset + 2, y)
        memory[count_offset] = count + 1
        if count < 2:
            return

        points = [
            (self._read_i16(memory, offset), self._read_i16(memory, offset + 2))
            for offset in range(first_sample, first_sample + 12, 4)
        ]
        center = self._circle_center(*points)
        if center is None:
            return
        cx, cy = center
        offset = self._CENTERS + zone * 4
        self._write_i16(memory, offset, self._wrap_delta(round(cx)))
        self._write_i16(memory, offset + 2, self._wrap_delta(round(cy)))
        memory[self._CENTER_MASK] |= 1 << zone

    @staticmethod
    def _circle_center(
        first: tuple[int, int], second: tuple[int, int], third: tuple[int, int]
    ) -> tuple[float, float] | None:
        """Find the center of the circle through three crossing samples."""
        x1, y1 = first
        x2, y2 = second
        x3, y3 = third
        determinant = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        if determinant == 0:
            return None

        norm1 = x1 * x1 + y1 * y1
        norm2 = x2 * x2 + y2 * y2
        norm3 = x3 * x3 + y3 * y3
        cx = (norm1 * (y2 - y3) + norm2 * (y3 - y1) + norm3 * (y1 - y2)) / determinant
        cy = (norm1 * (x3 - x2) + norm2 * (x1 - x3) + norm3 * (x2 - x1)) / determinant
        return cx, cy

    def _steer_to_zone(
        self, memory: bytearray, current: Depth, x: int, y: int, zone: int
    ) -> Depth | None:
        if not (memory[self._CENTER_MASK] & (1 << zone)):
            return None
        offset = self._CENTERS + zone * 4
        dx = self._wrap_delta(self._read_i16(memory, offset) - x)
        dy = self._wrap_delta(self._read_i16(memory, offset + 2) - y)
        if dx * dx + dy * dy < self._HOLD_RADIUS_KM**2:
            return self._best_hold_depth(memory, current, dx, dy)
        return self._best_route_depth(memory, current, dx, dy)

    def _best_route_depth(
        self, memory: bytearray, current: Depth, target_x: int, target_y: int
    ) -> Depth:
        best_depth = current
        best_progress = float("-inf")
        for depth in Depth:
            vx, vy = self._known_velocity(memory, depth)
            if vx == vy == 0:
                continue
            progress = vx * target_x + vy * target_y
            if progress > best_progress:
                best_progress, best_depth = progress, depth
        return best_depth

    def _best_hold_depth(
        self, memory: bytearray, current: Depth, target_x: int, target_y: int
    ) -> Depth:
        best_depth = current
        best_distance = float("inf")
        for depth in Depth:
            vx, vy = self._known_velocity(memory, depth)
            if vx == vy == 0:
                continue
            distance = (target_x - vx) ** 2 + (target_y - vy) ** 2
            if distance < best_distance:
                best_distance, best_depth = distance, depth
        return best_depth

    def _explore_depth(
        self, snapshot: JellyfishSnapshot, *, restart: bool = False
    ) -> Depth:
        memory = snapshot.memory
        mapped = len(memory) >= self._MAPPED_MEMORY
        slot = self._SEARCH_STATE if mapped else 0
        first_day_of_fall = 2 * DAYS_PER_SEASON + 1
        if restart or snapshot.day_of_year in (1, first_day_of_fall):
            memory[slot] = 0

        # The high nibble stores depth plus one. The low nibble stores days left.
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
        memory[slot] = ((depth.value + 1) << 4) | self.rng.randint(3, 7)
        return depth

    def _birthplace_delta(self, snapshot: JellyfishSnapshot) -> tuple[int, int]:
        # Offspring first act the day after birth, before their own first drift.
        first_turn = snapshot.age_days == (0 if self._is_founder(snapshot) else 1)
        vx, vy = snapshot.velocity
        memory = snapshot.memory
        return (
            self._track_birthplace_axis(memory, self._BIRTHPLACE_X, vx, first_turn),
            self._track_birthplace_axis(memory, self._BIRTHPLACE_Y, vy, first_turn),
        )

    def _track_birthplace_axis(
        self, memory: bytearray, offset: int, velocity: float, first_turn: bool
    ) -> int:
        end = offset + self._COORDINATE_BYTES
        position = 0
        if not first_turn:
            position = int.from_bytes(memory[offset:end], "little")
            drift = round(velocity * self._UNITS_PER_KM)
            position = (position + drift) % self._WORLD_UNITS
        memory[offset:end] = position.to_bytes(self._COORDINATE_BYTES, "little")
        half_world = self._WORLD_UNITS // 2
        return (half_world - position) % self._WORLD_UNITS - half_world

    def _known_velocity(self, memory: bytearray, depth: Depth) -> tuple[float, float]:
        offset = self._VELOCITIES + 4 * depth
        return self._read_i16(memory, offset) / 10, self._read_i16(
            memory, offset + 2
        ) / 10

    def _remember_drift(self, snapshot: JellyfishSnapshot, depth: Depth) -> None:
        memory = snapshot.memory
        offset = self._VELOCITIES + 4 * depth
        dx = self._read_i16(memory, offset)
        dy = self._read_i16(memory, offset + 2)
        if dx == dy == 0:
            dx = round(snapshot.velocity[0] * 10)
            dy = round(snapshot.velocity[1] * 10)
        self._write_i16(memory, self._LAST_DX, dx)
        self._write_i16(memory, self._LAST_DY, dy)

    @classmethod
    def _wrap_delta(cls, value: int) -> int:
        half_ocean = cls._OCEAN_KM // 2
        return (value + half_ocean) % cls._OCEAN_KM - half_ocean

    @staticmethod
    def _read_i16(memory: bytearray, offset: int) -> int:
        value = memory[offset] | (memory[offset + 1] << 8)
        return value - 65536 if value >= 32768 else value

    @staticmethod
    def _write_i16(memory: bytearray, offset: int, value: int) -> None:
        value %= 65536
        memory[offset] = value & 0xFF
        memory[offset + 1] = (value >> 8) & 0xFF
