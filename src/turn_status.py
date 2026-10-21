"""Persistent, sanitized Codex turn status and per-turn failure receipts."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from src.config import PROJECT_ROOT

STATE_DB = PROJECT_ROOT / '.turn-status.sqlite3'


@contextmanager
def db():
    connection = sqlite3.connect(STATE_DB)
    STATE_DB.chmod(0o600)
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS states (sid TEXT PRIMARY KEY, value TEXT NOT NULL)')
        with connection:
            yield connection
    finally:
        connection.close()


def get(sid):
    with db() as connection:
        row = connection.execute('SELECT value FROM states WHERE sid=?', (sid,)).fetchone()
    return json.loads(row[0]) if row else {}


def save(sid, value):
    with db() as connection:
        connection.execute('INSERT OR REPLACE INTO states VALUES (?,?)', (sid, json.dumps(value)))


def safe_error(error):
    # Never forward arbitrary gateway error text: it can include URLs or credentials.
    text = json.dumps(error, ensure_ascii=False).lower()
    if any(s in text for s in ('server_overloaded', 'at capacity', 'overloaded')):
        return {'code': 'capacity', 'reason': '模型服务当前容量不足'}
    if any(s in text for s in ('invalid_api_key', 'unauthorized', 'authentication', '401')):
        return {'code': 'authentication', 'reason': '模型服务认证失败'}
    if any(s in text for s in ('rate_limit', 'rate limit', '429')):
        return {'code': 'rate_limit', 'reason': '模型服务请求限流'}
    if any(s in text for s in ('context_length', 'context window', 'context limit')):
        return {'code': 'context_limit', 'reason': '模型上下文超出限制'}
    if any(s in text for s in ('timeout', 'timed out', 'connection', 'network')):
        return {'code': 'connection', 'reason': '模型服务连接失败或超时'}
    return {'code': 'model_error', 'reason': '模型调用失败，详细原因请查看终端'}


def lifecycle(event):
    if event.get('type') != 'event_msg':
        return None
    p = event.get('payload', {})
    kind = p.get('type')
    if kind in {'task_started', 'turn_started'}:
        state = 'running'
    elif kind in {'task_complete', 'task_completed'}:
        state = 'failed' if p.get('error') else 'idle'
    elif kind == 'turn_aborted':
        state = 'interrupted'
    elif kind == 'error':
        if p.get('will_retry') is True or p.get('retrying') is True:
            state = 'retrying'
        elif p.get('will_retry') is False or p.get('terminal') is True:
            state = 'failed'
        else:
            state = 'error_pending'  # wait for completion; retry intent is unknown
    else:
        return None
    result = {'type': 'result' if kind in {'task_complete', 'task_completed', 'turn_aborted'} else 'turn_status',
              'subtype': kind, 'activity': state, 'turn_id': p.get('turn_id'),
              'timestamp': event.get('timestamp')}
    if state in {'failed', 'retrying', 'error_pending'}:
        result['error'] = safe_error(p.get('error') or p)
    return result


def observe(sid, event, historical=False):
    new = lifecycle(event)
    if not new:
        return get(sid)
    old = get(sid)
    timestamp = new.get('timestamp') or old.get('timestamp') or ''
    if old.get('timestamp') and timestamp < old['timestamp']:
        return old
    turn = new.get('turn_id') or old.get('turn_id') or timestamp or 'unknown'
    if (new['activity'] != 'running' and old.get('turn_id') and new.get('turn_id')
            and turn != old['turn_id']):
        return old  # late completion of an older turn cannot overwrite current work
    same_turn = turn == old.get('turn_id')
    if same_turn and old.get('activity') == 'failed' and new['activity'] == 'idle':
        return old  # standalone error followed by an empty completion is still failure
    if same_turn and old.get('activity') == 'error_pending' and new['activity'] == 'idle':
        if not event.get('payload', {}).get('last_agent_message'):
            new.update(activity='failed', error=old['error'])
    notified = old.get('notified') if same_turn else None
    pending = (new['activity'] == 'failed' and not notified
               and (not historical or (same_turn and old.get('pending', False))))
    value = {**new, 'turn_id': turn, 'timestamp': timestamp, 'pending': pending,
             'notified': notified}
    save(sid, value)
    return value


def bootstrap(sid, path, end=None):
    """Recover recent status without replaying historical messages or notifications."""
    try:
        with Path(path).open('rb') as f:
            size = f.seek(0, 2) if end is None else end
            start = max(0, size - 2 * 1024 * 1024)
            f.seek(start)
            if start:
                f.readline()
            data = f.read(max(0, size - f.tell()))
        for line in data.splitlines():
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if lifecycle(event):
                observe(sid, event, historical=True)
    except OSError:
        pass
    return get(sid)


async def notify_failure(bot, sid, project, chat_id, topic_id):
    state = get(sid)
    if not state.get('pending') or state.get('activity') != 'failed':
        return
    reason = state['error']['reason']
    stamp = state.get('timestamp', '')
    try:
        from datetime import datetime, timezone, timedelta
        stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone(
            timezone(timedelta(hours=8))).strftime('%m-%d %H:%M:%S SGT')
    except (ValueError, TypeError):
        stamp = '时间未知'
    result = await bot.send_message(chat_id=chat_id, message_thread_id=topic_id,
        text=f'⚠️ {project}：{reason}。\n本轮已失败结束，等待你继续尝试或切换模型。\n{stamp}',
        parse_mode=None)
    current = get(sid)
    if current.get('turn_id') == state['turn_id']:
        current.update(pending=False, notified=result.message_id)
        save(sid, current)


def display_state(native_sid, screen_status):
    state = get(native_sid) if native_sid else {}
    # A visible busy/approval prompt is stronger than a stale completed-turn record.
    if screen_status == 'waiting_confirmation':
        return screen_status, state
    if screen_status == 'running':
        return ('retrying' if state.get('activity') == 'retrying' else 'running'), state
    if state.get('activity') == 'failed':
        return 'failed', state
    if screen_status == 'waiting_input':
        return 'waiting_input', state
    return screen_status, state
