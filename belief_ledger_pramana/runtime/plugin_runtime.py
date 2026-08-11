"""Lazy episode registry: host lifecycle, configuration reload, and episode resolution."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..application.lifecycle import LifecycleEventRecorder
from ..compatibility import CompatibilityReport, inspect_host
from ..config import (
    ConfigError,
    ConfigSnapshot,
    StatePaths,
    config_needs_reload,
    configured_config_path,
    ensure_state_directories,
    load_config,
    packaged_yaml,
    require_private_path,
    state_paths,
)
from ..context.inject import HermesRequestInjector
from ..contracts import EnforcementProfile, ProfileSelection, negotiate_profile
from ..events import (
    EventDraft,
    canonical_json,
    content_hash,
    utc_now,
)
from ..gate.classify import ActionPolicyRegistry
from ..ids import new_id
from ..infrastructure.sqlite_ledger import (
    SqliteEventWriter,
    SqliteLedgerMaintenance,
    SqliteLedgerReader,
    SqliteLlmBudgetLedger,
)
from ..ingestion.adapters import ToolAdapterRegistry
from ..models import (
    CompatibilityMode,
    Episode,
    Health,
)
from ..store import LedgerStore
from .episode_service import EpisodeService
from .errors import EpisodeResolutionError, RuntimeUnavailable
from .helpers import _action_policy_data, _clean, _correlation, _source_profile_data

logger = logging.getLogger(__name__)


class PluginRuntime:
    """Process-local registry; durable truth remains in the event store."""

    _CALLBACK_CACHE_LIMIT = 4_096
    _EPISODE_CONTEXT_CACHE_LIMIT = 1_024
    _DEFERRED_MAINTENANCE_LIMIT = 64

    def __init__(
        self,
        ctx: Any,
        *,
        compatibility: CompatibilityReport | None = None,
        hermes_home: Path | None = None,
    ) -> None:
        self.ctx = ctx
        self.compatibility = compatibility or inspect_host(ctx)
        self.host_capabilities = self.compatibility.host_capabilities()
        self.profile_selection: ProfileSelection | None = None
        self.hermes_home = hermes_home
        self.injector = HermesRequestInjector()
        self.adapters = ToolAdapterRegistry("hermes")
        self._initialize_lock = threading.RLock()
        self._registry_lock = threading.RLock()
        self._episode_locks: dict[str, threading.RLock] = {}
        self._turn_to_episode: OrderedDict[str, str] = OrderedDict()
        self._approval_to_episode: OrderedDict[str, str] = OrderedDict()
        self._begun_turns: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._turn_configs: dict[str, ConfigSnapshot] = {}
        self._queries: OrderedDict[str, str] = OrderedDict()
        self._recent_tool_results: OrderedDict[str, str] = OrderedDict()
        self._injection_failures: set[str] = set()
        self._episode_health_reasons: dict[str, list[str]] = {}
        self._maintenance_queue: OrderedDict[str, str] = OrderedDict()
        self._policy_cache: OrderedDict[str, ActionPolicyRegistry] = OrderedDict()
        self._source_profile_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._maintenance_active = False
        self._maintenance_idle = threading.Event()
        self._maintenance_idle.set()
        self._current_episode: contextvars.ContextVar[str] = contextvars.ContextVar(
            "belief_ledger_current_episode", default=""
        )
        self._config: ConfigSnapshot | None = None
        self.paths: StatePaths | None = None
        self.store: LedgerStore | None = None
        self.ledger_reader: SqliteLedgerReader | None = None
        self.event_writer: SqliteEventWriter | None = None
        self.llm_budget_ledger: SqliteLlmBudgetLedger | None = None
        self.maintenance: SqliteLedgerMaintenance | None = None
        self.lifecycle: LifecycleEventRecorder | None = None
        self.health = Health.HEALTHY
        self.health_reasons: list[str] = []
        self.transform_callback: Any | None = None
        self.loaded_module_path: str | None = None
        self.manifest_source: str | None = None

    @property
    def initialized(self) -> bool:
        return self.store is not None and self._config is not None

    def ensure_initialized(self) -> None:
        if self.initialized:
            return
        with self._initialize_lock:
            if self.initialized:
                return
            try:
                snapshot, paths = load_config(hermes_home=self.hermes_home)
            except ConfigError as exc:
                self._mark_configuration_degraded(f"invalid configuration: {exc}")
                # Safety fallback remains enforcing and is always reported; it is
                # used only so doctor/export can access diagnostics.
                defaults = packaged_yaml("defaults.yaml")
                paths = state_paths(self.hermes_home)
                try:
                    source = configured_config_path(self.hermes_home)
                except ConfigError:
                    # An out-of-scope configuration is never watched or loaded.
                    source = paths.config
                try:
                    mtime_ns = source.stat().st_mtime_ns
                except OSError:
                    mtime_ns = None
                snapshot = ConfigSnapshot(
                    defaults,
                    source,
                    (str(exc),),
                    content_hash(canonical_json(defaults)),
                    mtime_ns,
                )
                ensure_state_directories(paths)
            try:
                store = LedgerStore(
                    paths.database,
                    busy_timeout_ms=snapshot.settings.storage.busy_timeout_ms,
                    integrity_key_path=paths.integrity_key,
                )
                # The authenticated event chain alone cannot attest to mutable
                # projections. Replay fails closed if any projection diverges.
                store.verify_or_replay()
                require_private_path(store.database, "ledger database")
                require_private_path(paths.integrity_key, "ledger integrity key")
            except Exception as exc:
                self.health = Health.UNAVAILABLE
                self.health_reasons.append(f"database unavailable: {type(exc).__name__}: {exc}")
                raise RuntimeUnavailable(self.health_reasons[-1]) from exc
            enforcement = snapshot.section("enforcement")
            requested_profile = EnforcementProfile(str(enforcement["requested_profile"]))
            allow_downgrade = bool(enforcement["allow_diagnostic_downgrade"])
            selection = negotiate_profile(
                self.host_capabilities,
                requested_profile,
                allow_diagnostic_downgrade=allow_downgrade,
                observe_only=snapshot.mode == "observe",
            )
            if snapshot.mode == "enforce" and selection.missing and not allow_downgrade:
                self.health = Health.UNAVAILABLE
                reason = (
                    f"CAPABILITY_SHORTFALL:{requested_profile.value}:{','.join(selection.missing)}"
                )
                self.health_reasons.append(reason)
                raise RuntimeUnavailable(reason)
            self._config = snapshot
            self.paths = paths
            self.store = store
            self.ledger_reader = SqliteLedgerReader(store)
            self.event_writer = SqliteEventWriter(store)
            self.llm_budget_ledger = SqliteLlmBudgetLedger(store)
            self.maintenance = SqliteLedgerMaintenance(store)
            self.lifecycle = LifecycleEventRecorder(self.event_writer)
            self.profile_selection = selection
            if self.compatibility.mode is not CompatibilityMode.FULL:
                self.health = Health.DEGRADED
                self.health_reasons.extend(self.compatibility.errors or self.compatibility.warnings)

    @property
    def config(self) -> ConfigSnapshot:
        self.ensure_initialized()
        if self._config is None:
            raise RuntimeUnavailable("configuration is unavailable after initialization")
        return self._config

    def operational(self) -> bool:
        return self.compatibility.mode in {CompatibilityMode.FULL, CompatibilityMode.HOOK_CONTEXT}

    def policy_registry(self, config: ConfigSnapshot) -> ActionPolicyRegistry:
        with self._registry_lock:
            cached = self._policy_cache.get(config.digest)
            if cached is not None:
                self._policy_cache.move_to_end(config.digest)
                return cached
        registry = ActionPolicyRegistry(_action_policy_data(config.data))
        with self._registry_lock:
            self._policy_cache[config.digest] = registry
            self._policy_cache.move_to_end(config.digest)
            while len(self._policy_cache) > 8:
                self._policy_cache.popitem(last=False)
        return registry

    def source_profiles(self, config: ConfigSnapshot) -> dict[str, Any]:
        with self._registry_lock:
            cached = self._source_profile_cache.get(config.digest)
            if cached is not None:
                self._source_profile_cache.move_to_end(config.digest)
                return cached
        profiles = _source_profile_data(config.data)
        with self._registry_lock:
            self._source_profile_cache[config.digest] = profiles
            self._source_profile_cache.move_to_end(config.digest)
            while len(self._source_profile_cache) > 8:
                self._source_profile_cache.popitem(last=False)
        return profiles

    def checkpoint(self) -> None:
        """Run storage maintenance through the runtime composition boundary."""

        self.ensure_initialized()
        if self.maintenance is None:
            raise RuntimeUnavailable("ledger maintenance is unavailable after initialization")
        self.maintenance.checkpoint()

    @staticmethod
    def in_running_event_loop() -> bool:
        """Whether this synchronous callback is executing on an asyncio loop thread."""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        return True

    def begin_turn(self, **kwargs: Any) -> EpisodeService:
        service = self.service(**kwargs)
        turn_id = _clean(kwargs.get("turn_id"))
        if turn_id:
            with self._registry_lock:
                self._remember_callback(self._turn_to_episode, turn_id, service.episode_id)
        marker = turn_id or f"implicit:{new_id('turn')}"
        key = (service.episode_id, marker)
        with self._registry_lock:
            first = key not in self._begun_turns
            if first:
                self._begun_turns[key] = None
                self._trim_callback_caches()
        if first:
            self._reload_at_boundary()
            if self.store is None:
                raise RuntimeUnavailable("ledger store is unavailable after initialization")
            episode = self.store.get_episode(service.episode_id)
            if episode is None:
                raise RuntimeUnavailable("episode disappeared during turn initialization")
            now = utc_now()
            self.store.append_events(
                service.episode_id,
                [
                    EventDraft(
                        "EPISODE_TURN_STARTED",
                        "episode",
                        service.episode_id,
                        {"current_turn": episode.current_turn + 1, "updated_at": now},
                    )
                ],
                correlation=_correlation(kwargs),
                idempotency_key=f"turn:{service.episode_id}:{marker}",
            )
            with self._registry_lock:
                self._turn_configs[service.episode_id] = self.config
        query = _clean(kwargs.get("user_message"))
        if query:
            with self._registry_lock:
                self._remember_episode_context(self._queries, service.episode_id, query)
        current = self.service_for_id(service.episode_id)
        current.expire_retractions()
        return current

    def service(self, **kwargs: Any) -> EpisodeService:
        self.ensure_initialized()
        episode_id = self.resolve_episode_id(**kwargs)
        return self.service_for_id(episode_id)

    def service_for_id(self, episode_id: str) -> EpisodeService:
        self.ensure_initialized()
        if (
            self.store is None
            or self.lifecycle is None
            or self.ledger_reader is None
            or self.event_writer is None
            or self.llm_budget_ledger is None
        ):
            raise RuntimeUnavailable("ledger services are unavailable after initialization")
        with self._registry_lock:
            snapshot = self._turn_configs.get(episode_id, self.config)
        self._current_episode.set(episode_id)
        return EpisodeService(self, episode_id, self.store, snapshot)

    def current_service(self) -> EpisodeService:
        episode_id = self._current_episode.get()
        if episode_id and self.store is not None:
            current = self.store.get_episode(episode_id)
            if current is not None and current.state == "active":
                return self.service_for_id(episode_id)
        self.ensure_initialized()
        if self.store is None:
            raise RuntimeUnavailable("ledger store is unavailable after initialization")
        active = [episode for episode in self.store.list_episodes() if episode.state == "active"]
        if not active:
            raise EpisodeResolutionError("no active ledger episode")
        return self.service_for_id(active[0].id)

    def resolve_episode_id(self, **kwargs: Any) -> str:
        self.ensure_initialized()
        if self.store is None:
            raise RuntimeUnavailable("ledger store is unavailable after initialization")
        session_id = _clean(kwargs.get("session_id"))
        session_key = _clean(kwargs.get("session_key"))
        turn_id = _clean(kwargs.get("turn_id"))
        task_id = _clean(kwargs.get("task_id"))
        if session_id:
            key = f"session:{session_id}"
        elif session_key:
            with self._registry_lock:
                approved_episode = self._approval_to_episode.get(session_key)
                if approved_episode:
                    self._approval_to_episode.move_to_end(session_key)
                    return approved_episode
            key = f"approval:{session_key}"
        elif turn_id:
            with self._registry_lock:
                mapped_episode = self._turn_to_episode.get(turn_id)
                if mapped_episode:
                    self._turn_to_episode.move_to_end(turn_id)
                    return mapped_episode
            key = f"task:{task_id}" if task_id else f"oneshot:{new_id('episode')}"
        elif task_id:
            key = f"task:{task_id}"
        else:
            # Never reuse an anonymous one-shot identity across callback calls.
            key = f"oneshot:{new_id('episode')}"

        existing = self.store.get_episode_by_key(key)
        if existing is not None:
            return existing.id
        with self._registry_lock:
            existing = self.store.get_episode_by_key(key)
            if existing is not None:
                return existing.id
            now = utc_now()
            episode_id = new_id("episode")
            episode = Episode(
                id=episode_id,
                key=key,
                session_id=session_id,
                task_id=task_id,
                platform=_clean(kwargs.get("platform")),
                model=_clean(kwargs.get("model")),
                default_stakes=self.config.default_stakes,
                current_turn=0,
                created_at=now,
                updated_at=now,
                compatibility_mode=self.compatibility.mode,
            )
            try:
                self.store.create_episode(episode, _correlation(kwargs))
            except sqlite3.IntegrityError:
                concurrent = self.store.get_episode_by_key(key)
                if concurrent is None:
                    raise
                return concurrent.id
            self._episode_locks[episode_id] = threading.RLock()
            return episode_id

    def bind_approval_session_key(self, session_key: str, episode_id: str) -> None:
        if session_key:
            with self._registry_lock:
                self._remember_callback(self._approval_to_episode, session_key, episode_id)

    @contextmanager
    def episode_lock(self, episode_id: str) -> Iterator[None]:
        with self._registry_lock:
            lock = self._episode_locks.setdefault(episode_id, threading.RLock())
        with lock:
            yield

    def query_for(self, episode_id: str) -> str:
        with self._registry_lock:
            query = self._queries.get(episode_id, "")
            if query:
                self._queries.move_to_end(episode_id)
            return query

    def set_recent_tool_result(self, episode_id: str, result: str) -> None:
        with self._registry_lock:
            self._remember_episode_context(self._recent_tool_results, episode_id, result[:2_000])

    def recent_tool_result(self, episode_id: str) -> str:
        with self._registry_lock:
            result = self._recent_tool_results.get(episode_id, "")
            if result:
                self._recent_tool_results.move_to_end(episode_id)
            return result

    def schedule_context_maintenance(self, episode_id: str, query: str) -> None:
        """Run optional model-assisted promotion and audits off an async callback loop."""

        with self._registry_lock:
            self._maintenance_queue[episode_id] = query
            self._maintenance_queue.move_to_end(episode_id)
            while len(self._maintenance_queue) > self._DEFERRED_MAINTENANCE_LIMIT:
                self._maintenance_queue.popitem(last=False)
            if self._maintenance_active:
                return
            self._maintenance_active = True
            self._maintenance_idle.clear()
        try:
            threading.Thread(
                target=self._drain_context_maintenance,
                name="belief-ledger-maintenance",
                daemon=True,
            ).start()
        except RuntimeError:
            with self._registry_lock:
                self._maintenance_active = False
                self._maintenance_idle.set()
            raise

    def wait_for_context_maintenance(self, timeout: float = 5.0) -> bool:
        """Wait for deferred maintenance; useful to orderly lifecycle code and tests."""

        return self._maintenance_idle.wait(timeout)

    def _drain_context_maintenance(self) -> None:
        while True:
            with self._registry_lock:
                if not self._maintenance_queue:
                    self._maintenance_active = False
                    self._maintenance_idle.set()
                    return
                episode_id, query = self._maintenance_queue.popitem(last=False)
            try:
                service = self.service_for_id(episode_id)
                episode = service.store.get_episode(episode_id)
                if episode is not None and episode.state == "active":
                    service.run_deferred_context_maintenance(query)
            except Exception:
                logger.exception("belief-ledger deferred context maintenance failed")

    def _remember_callback(self, cache: OrderedDict[str, str], key: str, episode_id: str) -> None:
        cache[key] = episode_id
        cache.move_to_end(key)
        self._trim_callback_caches()

    def _trim_callback_caches(self) -> None:
        while len(self._turn_to_episode) > self._CALLBACK_CACHE_LIMIT:
            self._turn_to_episode.popitem(last=False)
        while len(self._approval_to_episode) > self._CALLBACK_CACHE_LIMIT:
            self._approval_to_episode.popitem(last=False)
        while len(self._begun_turns) > self._CALLBACK_CACHE_LIMIT:
            self._begun_turns.popitem(last=False)

    def _remember_episode_context(
        self, cache: OrderedDict[str, str], episode_id: str, value: str
    ) -> None:
        cache[episode_id] = value
        cache.move_to_end(episode_id)
        while len(cache) > self._EPISODE_CONTEXT_CACHE_LIMIT:
            cache.popitem(last=False)

    # Health and injection-failure state is read and written from concurrent host callbacks,
    # and `finalize` mutates the very same containers under `_registry_lock`. Every accessor
    # below takes that lock so a read-modify-write cannot interleave with a finalization.
    def mark_injection_failure(self, episode_id: str, reason: str) -> None:
        with self._registry_lock:
            self._injection_failures.add(episode_id)
            reasons = self._episode_health_reasons.setdefault(episode_id, [])
            entry = f"context injection failed: {reason}"
            if entry not in reasons:
                reasons.append(entry)

    def clear_injection_failure(self, episode_id: str) -> None:
        with self._registry_lock:
            self._injection_failures.discard(episode_id)
            reasons = self._episode_health_reasons.get(episode_id)
            if reasons is None:
                return
            retained = [
                reason for reason in reasons if not reason.startswith("context injection failed:")
            ]
            if retained:
                self._episode_health_reasons[episode_id] = retained
            else:
                self._episode_health_reasons.pop(episode_id, None)

    def mark_global_failure(self, component: str, reason: str) -> None:
        with self._registry_lock:
            if self.health is not Health.UNAVAILABLE:
                self.health = Health.DEGRADED
            self.health_reasons.append(f"{component} failed: {reason}")

    def _mark_configuration_degraded(self, reason: str) -> None:
        with self._registry_lock:
            if self.health is not Health.UNAVAILABLE:
                self.health = Health.DEGRADED
            self.health_reasons = [
                item
                for item in self.health_reasons
                if not item.startswith(("invalid configuration:", "configuration reload rejected:"))
            ]
            self.health_reasons.append(reason)

    def _clear_configuration_degradation(self) -> None:
        with self._registry_lock:
            self.health_reasons = [
                item
                for item in self.health_reasons
                if not item.startswith(("invalid configuration:", "configuration reload rejected:"))
            ]
            if self.health is Health.UNAVAILABLE:
                return
            if self.compatibility.mode is not CompatibilityMode.FULL:
                self.health = Health.DEGRADED
            elif not self.health_reasons:
                self.health = Health.HEALTHY

    def injection_failed(self, episode_id: str) -> bool:
        with self._registry_lock:
            return episode_id in self._injection_failures

    def finalize(self, episode_id: str, *, state: str = "finalized", **kwargs: Any) -> None:
        self.ensure_initialized()
        if self.store is None or self.lifecycle is None:
            raise RuntimeUnavailable("ledger lifecycle is unavailable after initialization")
        episode = self.store.get_episode(episode_id)
        if episode is None:
            raise EpisodeResolutionError("cannot finalize an unknown ledger episode")
        archived_key = f"closed:{episode.id}:{episode.key}"
        self.lifecycle.record(
            episode_id,
            "EPISODE_FINALIZED" if state == "finalized" else "EPISODE_RESET",
            "episode",
            episode_id,
            {"state": state, "updated_at": utc_now(), "episode_key": archived_key},
            correlation=_correlation(kwargs),
        )
        with self._registry_lock:
            self._turn_configs.pop(episode_id, None)
            self._queries.pop(episode_id, None)
            self._recent_tool_results.pop(episode_id, None)
            self._injection_failures.discard(episode_id)
            self._episode_health_reasons.pop(episode_id, None)
            self._episode_locks.pop(episode_id, None)
            self._begun_turns = OrderedDict(
                (key, None) for key in self._begun_turns if key[0] != episode_id
            )
            for turn_id, mapped in list(self._turn_to_episode.items()):
                if mapped == episode_id:
                    self._turn_to_episode.pop(turn_id, None)
            for session_key, mapped in list(self._approval_to_episode.items()):
                if mapped == episode_id:
                    self._approval_to_episode.pop(session_key, None)
            if self._current_episode.get() == episode_id:
                self._current_episode.set("")

    def _reload_at_boundary(self) -> None:
        if self._config is None or not config_needs_reload(self._config):
            return
        try:
            snapshot, paths = load_config(hermes_home=self.hermes_home)
        except (ConfigError, OSError) as exc:
            # OSError as well as ConfigError: an extension file that disappeared between
            # snapshots must degrade the runtime, not escape into a host callback.
            self._mark_configuration_degraded(f"configuration reload rejected: {exc}")
            return
        with self._registry_lock:
            if self.paths is not None and paths.database != self.paths.database:
                self.health = Health.DEGRADED
                self.health_reasons.append("database path changed; restart is required")
                return
            self._config = snapshot
        self._clear_configuration_degradation()
