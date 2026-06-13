"""Pure speculative future data for Permit Office decisions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from . import decisions
from .cache_keys import GenerationToken, state_fingerprint
from .dirty import DISTRICTS, LINES, POINTS, ZONES, DistrictBitIndex


@dataclass
class FutureStateSnapshot:
    """Deep-copied state used only for speculative expansion."""

    state: Any
    districts: dict[str, Any]
    docket: list[Any]
    active_features: list[Any] = field(default_factory=list)
    projects: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def capture(
        cls,
        state: Any,
        districts: Mapping[str, Any],
        docket: Iterable[Any],
        active_features: Iterable[Any] | None = None,
        projects: Mapping[str, Any] | None = None,
    ) -> "FutureStateSnapshot":
        return cls(
            deepcopy(state),
            deepcopy(dict(districts or {})),
            deepcopy(list(docket or ())),
            deepcopy(list(active_features or ())),
            deepcopy(dict(projects or {})),
        )


@dataclass(frozen=True)
class DecisionDelta:
    """Compact speculative change anchored to parent/result hashes."""

    parent_hash: str
    resulting_state_hash: str
    city_delta: dict[str, int] = field(default_factory=dict)
    district_deltas: dict[str, dict[str, int]] = field(default_factory=dict)
    feature_updates: dict[str, dict[str, object]] = field(default_factory=dict)
    item_status: str = ""
    affected_cell_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecisionFutureNode:
    """One speculative action from one parent state hash."""

    parent_hash: str
    action_key: str
    item_id: str
    resulting_state_hash: str
    generation: GenerationToken
    legal: bool = True
    delta: DecisionDelta | None = None
    cost_preview: dict[str, int] = field(default_factory=dict)
    report_preview: str = ""
    affected_district_bits: int = 0
    dirty_layer_bits: int = 0
    target_cell_ids: tuple[str, ...] = ()
    spillover_cell_ids: tuple[str, ...] = ()
    district_deltas: dict[str, dict[str, int]] = field(default_factory=dict)
    feature_updates: dict[str, dict[str, object]] = field(default_factory=dict)
    redraw_plan: Any = None
    validation_notes: tuple[str, ...] = ()


@dataclass
class EvaluatedState:
    """Transposition table entry for one compact state hash."""

    state_hash: str
    legal_actions: tuple[tuple[str, str], ...] = ()
    outgoing_future_nodes: dict[tuple[str, str], DecisionFutureNode] = field(default_factory=dict)


class DecisionFutureCache:
    """One-ply speculative decision cache for the current command generation."""

    ACTIONS = ("approve", "approve_mitigated", "deny")

    def __init__(self, generation: GenerationToken, *, game_id: str = "", seed: int = 2026):
        self.generation = generation
        self.game_id = game_id
        self.seed = seed
        self.current_state_hash = ""
        self.nodes: dict[tuple[str, str, str], DecisionFutureNode] = {}
        self.transposition: dict[str, EvaluatedState] = {}

    def build_one_ply(
        self,
        state: Any,
        districts: Mapping[str, Any],
        docket: Iterable[Any],
        active_features: Iterable[Any] | None = None,
        projects: Mapping[str, Any] | None = None,
        spillover_provider=None,
    ) -> list[DecisionFutureNode]:
        """Generate one speculative node per supported action for open docket items."""

        items = [item for item in docket or () if getattr(item, "status", "open") in ("open", "inspected", "active", "carried")]
        features = list(active_features or ())
        project_map = dict(projects or {})
        fingerprint = state_fingerprint(state, districts, items, features, project_map, game_id=self.game_id)
        parent_hash = fingerprint.rules_hash
        self.current_state_hash = parent_hash
        bit_index = DistrictBitIndex.from_cell_ids((districts or {}).keys())
        generated: list[DecisionFutureNode] = []
        evaluated = self.transposition.setdefault(parent_hash, EvaluatedState(parent_hash))

        for item in items:
            target_ids = _known_ids(getattr(item, "target_cell_ids", ()) or (), districts)
            spillover_ids = _known_ids(spillover_provider(item) if spillover_provider is not None else (), districts)
            for action in self.ACTIONS:
                try:
                    node = self._build_node(
                        parent_hash,
                        state,
                        districts,
                        items,
                        item,
                        action,
                        target_ids,
                        spillover_ids,
                        features,
                        project_map,
                        bit_index,
                    )
                except Exception as exc:
                    node = self._failed_node(parent_hash, item, action, target_ids, spillover_ids, bit_index, exc)
                self.nodes[(parent_hash, item.item_id, action)] = node
                evaluated.outgoing_future_nodes[(item.item_id, action)] = node
                generated.append(node)

        evaluated.legal_actions = tuple((node.item_id, node.action_key) for node in generated if node.legal)
        return generated

    def lookup(self, parent_hash: str, item_id: str, action_key: str) -> DecisionFutureNode | None:
        return self.nodes.get((parent_hash, item_id, action_key))

    def promote_chosen(self, chosen: DecisionFutureNode) -> None:
        """Keep the chosen node and evict its impossible siblings."""

        for key in list(self.nodes):
            parent_hash, item_id, action_key = key
            if parent_hash == chosen.parent_hash and (item_id, action_key) != (chosen.item_id, chosen.action_key):
                del self.nodes[key]
        self.nodes[(chosen.parent_hash, chosen.item_id, chosen.action_key)] = chosen
        self.current_state_hash = chosen.resulting_state_hash

    def _build_node(
        self,
        parent_hash,
        state,
        districts,
        docket,
        item,
        action,
        target_ids,
        spillover_ids,
        active_features,
        projects,
        bit_index,
    ) -> DecisionFutureNode:
        snapshot = FutureStateSnapshot.capture(state, districts, docket, active_features, projects)
        item_copy = next(candidate for candidate in snapshot.docket if candidate.item_id == item.item_id)
        mitigated = action == "approve_mitigated"
        result = decisions.resolve_decision(
            snapshot.state,
            item_copy,
            snapshot.districts,
            action,
            target_ids,
            spillover_ids,
            seed=self.seed,
            mitigated=mitigated,
            active_features=snapshot.active_features,
            projects=snapshot.projects,
        )
        legal = bool(getattr(result, "ok", False))
        result_hash = state_fingerprint(
            snapshot.state,
            snapshot.districts,
            snapshot.docket,
            snapshot.active_features,
            snapshot.projects,
            game_id=self.game_id,
        ).rules_hash
        affected = tuple(getattr(result, "affected_cell_ids", ()) or target_ids or ())
        dirty_bits = DISTRICTS | _feature_layer_bit(getattr(item, "geometry_type", ""))
        return DecisionFutureNode(
            parent_hash=parent_hash,
            action_key=action,
            item_id=item.item_id,
            resulting_state_hash=result_hash,
            generation=self.generation,
            legal=legal,
            delta=DecisionDelta(
                parent_hash=parent_hash,
                resulting_state_hash=result_hash,
                city_delta=deepcopy(getattr(result, "city_delta", {}) or {}),
                district_deltas=deepcopy(getattr(result, "district_deltas", {}) or {}),
                feature_updates=deepcopy(getattr(result, "feature_updates", {}) or {}),
                item_status=getattr(result, "item_status", ""),
                affected_cell_ids=tuple(affected),
            ),
            cost_preview=_cost_preview(state, snapshot.state),
            report_preview=getattr(result, "report", ""),
            affected_district_bits=bit_index.to_bits(affected),
            dirty_layer_bits=dirty_bits,
            target_cell_ids=tuple(target_ids),
            spillover_cell_ids=tuple(spillover_ids),
            district_deltas=deepcopy(getattr(result, "district_deltas", {}) or {}),
            feature_updates=deepcopy(getattr(result, "feature_updates", {}) or {}),
            redraw_plan={
                "feature_layer_key": _feature_layer_key(getattr(item, "geometry_type", "")),
                "affected_cell_ids": tuple(affected),
                "requires_district_rehydrate": bool(affected),
            },
            validation_notes=() if legal else (getattr(result, "report", "") or "not legal",),
        )

    def _failed_node(self, parent_hash, item, action, target_ids, spillover_ids, bit_index, exc) -> DecisionFutureNode:
        affected = tuple(target_ids or ())
        dirty_bits = DISTRICTS | _feature_layer_bit(getattr(item, "geometry_type", ""))
        return DecisionFutureNode(
            parent_hash=parent_hash,
            action_key=action,
            item_id=getattr(item, "item_id", ""),
            resulting_state_hash=parent_hash,
            generation=self.generation,
            legal=False,
            delta=DecisionDelta(
                parent_hash=parent_hash,
                resulting_state_hash=parent_hash,
                affected_cell_ids=affected,
            ),
            affected_district_bits=bit_index.to_bits(affected),
            dirty_layer_bits=dirty_bits,
            target_cell_ids=tuple(target_ids),
            spillover_cell_ids=tuple(spillover_ids),
            redraw_plan={
                "feature_layer_key": _feature_layer_key(getattr(item, "geometry_type", "")),
                "affected_cell_ids": affected,
                "requires_district_rehydrate": bool(affected),
            },
            validation_notes=(f"speculative resolve failed: {type(exc).__name__}: {exc}",),
        )


def _cost_preview(before, after) -> dict[str, int]:
    return {
        "ap": int(getattr(after, "ap", 0) or 0) - int(getattr(before, "ap", 0) or 0),
        "money": int(getattr(after, "money", 0) or 0) - int(getattr(before, "money", 0) or 0),
    }


def _feature_layer_key(geometry_type: str) -> str:
    return {
        "POINT": "points",
        "LINE": "lines",
        "POLYGON": "zones",
    }.get(str(geometry_type or "").upper(), "")


def _feature_layer_bit(geometry_type: str) -> int:
    return {
        "POINT": POINTS,
        "LINE": LINES,
        "POLYGON": ZONES,
    }.get(str(geometry_type or "").upper(), 0)


def _known_ids(values, districts) -> tuple[str, ...]:
    seen = set()
    out = []
    for value in values or ():
        try:
            known = value in districts
            already_seen = value in seen
        except TypeError:
            continue
        if known and not already_seen:
            seen.add(value)
            out.append(value)
    return tuple(out)
