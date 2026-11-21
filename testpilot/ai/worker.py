"""Persistent attempts: short fenced checkpoints, leased ownership, no automatic retry."""
from __future__ import annotations

import threading
import time
from hashlib import sha256
from typing import Callable
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import BigInteger, cast, or_, select
from testpilot.contracts import now

_POLL_SECONDS = 0.25
_LEASE = '300'
LEASE_SECONDS = int(_LEASE)
# Продление lease: обычный интервал и backoff после сбоев БД (1, 2, 5, 10, затем 30 с).
_HEARTBEAT_INTERVAL_SECONDS = 30.0
_HEARTBEAT_BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0)


def business_success(results) -> bool | None:
    """Агрегат подтверждённых business-проверок прогона.

    None — business-проверок не было (или они не дали подтверждённого результата),
    True — все подтверждённые прошли, False — хотя бы одна провалилась.
    """
    checked = [item.get('business_success') for item in results or []
               if item.get('business_success') is not None]
    return all(checked) if checked else None


_stop = threading.Event()
_thread: threading.Thread | None = None


def begin_attempt(row) -> None:
    """Mark a row as a fenced attempt; the caller's transaction owns the lock and the commit."""
    from testpilot.db import uid
    row.attempt_id = uid()
    row.attempt_version = int(row.attempt_version or 0) + 1
    row.started_at = row.started_at or now()
    row.last_heartbeat_at = now()
    row.lease_expires_at = str(int(time.time()) + LEASE_SECONDS)


def claim(factory):
    """Short transaction: claim exactly one queued suite; second worker gets None."""
    from testpilot.db import AISuiteRow
    with factory() as session:
        row = session.scalar(
            select(AISuiteRow)
            .where(AISuiteRow.status == 'queued')
            .order_by(AISuiteRow.created_at, AISuiteRow.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        row.status = 'running'
        begin_attempt(row)
        session.commit()
        return {'suite_id': row.id, 'payload': dict(row.payload or {}), 'project_id': row.project_id,
                'attempt_id': row.attempt_id, 'attempt_version': row.attempt_version}


def recover_interrupted(factory) -> int:
    """Recover expired attempts without treating a still-live lease as a crash."""
    return _reap_expired(factory)


def _attempt_query(claim_data):
    from testpilot.db import AISuiteRow
    return select(AISuiteRow).where(
        AISuiteRow.id == claim_data['suite_id'],
        AISuiteRow.status == 'running',
        AISuiteRow.attempt_id == claim_data['attempt_id'],
        AISuiteRow.attempt_version == claim_data['attempt_version'],
        cast(AISuiteRow.lease_expires_at, BigInteger) > int(time.time()),
    )


def _cancelled(factory, claim_data) -> bool:
    with factory() as session:
        row = session.scalar(_attempt_query(claim_data))
        return row is None or bool(row.cancel_requested)


def _result_key(result):
    return (result['proposal_index'], result['case_index'], result.get('step_index'))


def _merge_progress(session, suite_id, payload, result, shots):
    """Upsert one stable case/step and its content-addressed evidence under the row lock."""
    from testpilot.db import EvidenceRow
    key = _result_key(result)
    field = 'step_results' if result.get('step_index') is not None else 'results'
    results = list(payload.get(field) or [])
    position = next((index for index, item in enumerate(results) if _result_key(item) == key), None)
    merged = {**(results[position] if position is not None else {}), **result}
    for content in shots or []:
        if not content:
            continue
        digest = sha256(content).hexdigest()
        stable_key = ':'.join('' if value is None else str(value) for value in key)
        eid = str(uuid5(NAMESPACE_URL, f'{suite_id}:{stable_key}:{digest}'))
        if session.get(EvidenceRow, eid) is None:
            session.add(EvidenceRow(id=eid, suite_id=suite_id, content=content))
            session.flush()
        merged['evidence_id'] = eid
    if position is None:
        results.append(merged)
    else:
        results[position] = merged
    payload[field] = results
    if merged.get('evidence_id'):
        result['evidence_id'] = merged['evidence_id']
    payload['counts'] = {
        state: sum(item.get('status') == state for item in payload.get('results') or [])
        for state in ('passed', 'failed', 'error', 'skipped')
    }


def _checkpoint_progress(factory, claim_data, result, shots):
    """Commit before the next action; a lost/expired fence never writes progress."""
    with factory() as session:
        row = session.scalar(_attempt_query(claim_data).with_for_update())
        if row is None:
            return False
        payload = dict(row.payload or {})
        _merge_progress(session, row.id, payload, result, shots)
        payload['progress_saved_at'] = now()
        row.payload = payload
        session.commit()
        return True


def _reap_expired(factory) -> int:
    """Atomically reap only expired attempts; heartbeat/finalize use the same row lock."""
    from testpilot.db import AISuiteRow
    with factory() as session:
        rows = list(session.scalars(
            select(AISuiteRow).where(
                AISuiteRow.status == 'running',
                or_(AISuiteRow.lease_expires_at.is_(None),
                    cast(AISuiteRow.lease_expires_at, BigInteger) <= int(time.time())),
            ).with_for_update(skip_locked=True)
        ))
        for row in rows:
            row.status = 'interrupted'
            row.finished_at = now()
            row.lease_expires_at = None
            row.payload = {
                **dict(row.payload or {}), 'status': 'interrupted', 'finished_at': row.finished_at,
                'message': 'Прогон прерван по истёкшему lease; автоматический повтор не выполняется',
            }
        session.commit()
        return len(rows)


def heartbeat(factory, claim_data, stop_event) -> None:
    """Renew the lease in short transactions while an attempt runs.

    Сбой БД — повод повторить с backoff, а не прекратить продление: иначе прогон,
    живущий дольше lease, молча помечается interrupted. Прекращаем только когда
    строка попытки исчезла (fence другой попытки/истёкший lease), а факт потери
    продления сохраняем в payload для диагностики, не трогая результаты.
    """
    delay = _HEARTBEAT_INTERVAL_SECONDS
    failures = 0
    lost_at = None
    while not stop_event.wait(delay):
        try:
            with factory() as session:
                row = session.scalar(_attempt_query(claim_data).with_for_update())
                if row is None:
                    return
                if failures:
                    payload = dict(row.payload or {})
                    payload['heartbeat_errors'] = int(payload.get('heartbeat_errors') or 0) + failures
                    payload['heartbeat_lost'] = lost_at
                    row.payload = payload
                row.last_heartbeat_at = now()
                row.lease_expires_at = str(int(time.time()) + LEASE_SECONDS)
                session.commit()
        except Exception:
            failures += 1
            lost_at = now()
            delay = _HEARTBEAT_BACKOFF_SECONDS[min(failures - 1, len(_HEARTBEAT_BACKOFF_SECONDS) - 1)]
            continue
        failures = 0
        lost_at = None
        delay = _HEARTBEAT_INTERVAL_SECONDS


def execute_claim(factory, claim_data, *, executor: Callable | None = None, on_progress=None) -> None:
    """Execute outside transactions; retain committed progress on cancellation or crash."""
    from testpilot.db import ProjectRow
    from testpilot.ai.api import _profile_for_suite
    suite_id = claim_data['suite_id']
    stop_heartbeat = threading.Event()
    keeper = threading.Thread(target=heartbeat, args=(factory, claim_data, stop_heartbeat),
                              name='ai-run-heartbeat', daemon=True)
    keeper.start()
    try:
        with factory() as session:
            row = session.scalar(_attempt_query(claim_data))
            if row is None:
                return
            project = session.get(ProjectRow, claim_data['project_id'])
            from testpilot.ai.api import _target_identity
            approved = (dict(row.payload or {}).get('approval') or {}).get('target_identity')
            if approved != _target_identity(session, project):
                raise RuntimeError('Цель/подключение изменились после enqueue; требуется повторное согласование')
            if project and (project.odata_url or project.web_url):
                profile = _profile_for_suite(row, project, session)
            else:
                from types import SimpleNamespace
                profile = SimpleNamespace(odata_url='', web_url=None)
            suite = dict(row.payload or {})
        def progress(result, shots):
            saved = _checkpoint_progress(factory, claim_data, result, shots)
            if saved and on_progress is not None:
                on_progress(result, shots)
            return saved

        if executor is None:
            from testpilot.ai.executor import execute_1c
            executor = execute_1c
        results, shots = executor(
            suite, profile, should_cancel=lambda: _cancelled(factory, claim_data),
            on_progress=progress,
        )
        with factory() as session:
            row = session.scalar(_attempt_query(claim_data).with_for_update())
            if row is None:
                return
            payload = dict(row.payload or {})
            case_shots = {}
            for index, content in shots:
                case_shots.setdefault(index, []).append(content)
            for index, result in enumerate(results):
                _merge_progress(session, suite_id, payload, result, case_shots.get(index, []))
            cancelled = bool(row.cancel_requested)
            status = 'cancelled' if cancelled else 'completed'
            data = {**payload, 'status': status, 'finished_at': now(),
                    'business_success': business_success(payload.get('results') or results)}
            if cancelled:
                data['message'] = 'Отменено до следующего шага; уже выполненные шаги сохранены'
            row.payload = data
            row.status = status
            row.finished_at = now()
            row.lease_expires_at = None
            session.commit()
    except Exception as exc:
        with factory() as session:
            row = session.scalar(_attempt_query(claim_data).with_for_update())
            if row is not None:
                data = {**dict(row.payload or {}), 'status': 'error', 'message': str(exc)[:500],
                        'finished_at': now(), 'business_success': None}
                data['counts'] = {
                    state: sum(item.get('status') == state for item in data.get('results') or [])
                    for state in ('passed', 'failed', 'error', 'skipped')
                }
                row.payload = data
                row.status = 'error'
                row.finished_at = now()
                row.lease_expires_at = None
                session.commit()
    finally:
        stop_heartbeat.set()
        keeper.join(timeout=2)


def worker_loop(factory) -> None:
    while not _stop.is_set():
        _reap_expired(factory)
        claim_data = claim(factory)
        if claim_data is None:
            _stop.wait(_POLL_SECONDS)
            continue
        execute_claim(factory, claim_data)


def start(factory) -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    recover_interrupted(factory)
    _stop.clear()
    _thread = threading.Thread(target=worker_loop, args=(factory,), name='ai-run-worker', daemon=True)
    _thread.start()


def stop() -> None:
    global _thread
    _stop.set()
    if _thread:
        _thread.join(timeout=10)
        if not _thread.is_alive():
            _thread = None
