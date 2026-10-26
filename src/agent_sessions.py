"""Local agent discovery and explicit, receipt-aware terminal messaging.

Session keys include PID and process start time; a replacement CLI is never
silently selected. Summaries are public conversation excerpts, not reasoning.
"""
import asyncio
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

from src import backends, procinfo
from src.config import PROJECT_ROOT

RECEIPTS = PROJECT_ROOT / '.agent-message-receipts.sqlite3'
_locks = {}


def public_text(event):
    payload = event.get('payload', {})
    if event.get('type') == 'response_item' and payload.get('type') == 'message':
        role, content = payload.get('role'), payload.get('content', [])
    elif event.get('type') in {'user', 'assistant'}:
        role = event['type']
        content = event.get('message', {}).get('content', [])
    else:
        normalized = backends.normalize_codex(event)
        if not normalized or normalized.get('type') not in {'user', 'assistant'}:
            return None, ''
        role = normalized['type']
        content = normalized.get('message', {}).get('content', [])
    if role not in {'user', 'assistant'}:
        return None, ''
    if isinstance(content, str):
        return role, content
    return role, '\n'.join(c.get('text', '') for c in content
                           if c.get('type') in {'text', 'input_text', 'output_text'})


def tail_events(path, offset=None, limit=262144):
    if not path:
        return []
    try:
        with Path(path).open('rb') as f:
            size = f.seek(0, 2)
            start = max(offset or 0, size - limit)
            f.seek(start)
            if start and start != offset:
                f.readline()  # discard incomplete first record
            lines = f.read(limit).splitlines()
        events = []
        for line in lines:
            try:
                events.append(json.loads(line))
            except (ValueError, UnicodeError):
                pass
        return events
    except OSError:
        return []


_ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)')
# 输入框里的灰色文字（ESC[2m）是 Claude 的输入建议 / Codex 的占位提示，不是用户草稿
_DIM_TEXT = re.compile(r'\x1b\[2m[^\x1b]*')


def _prompt_is_empty(raw_line):
    text = _ANSI.sub('', _DIM_TEXT.sub('', raw_line))
    return re.match(r'^\s*[›❯]\s*(Ask Codex to do anything)?\s*$', text)


def screen_state(screen):
    """Best-effort UI evidence; absence of a spinner does not mean idle.

    screen 可以带颜色（capture-pane -e）：靠它区分输入框里的灰色建议和用户真正打的草稿。
    """
    raw_lines = screen.splitlines()[-35:]
    lines = [_ANSI.sub('', line) for line in raw_lines]
    for line in reversed(lines):
        if re.search(r'esc to interrupt|ctrl\+c to interrupt', line, re.I):
            return 'running', 'terminal shows interruptible work'
        if re.search(r'Would you like to proceed|Do you want to proceed|Allow this|Approve once|Do you trust', line, re.I):
            return 'waiting_confirmation', 'terminal shows a confirmation prompt'
    # Newer Claude Code no longer prints "esc to interrupt"; its spinner line
    # ("· Simmering… (18s · ↓ 188 tokens)") is the evidence, while the empty
    # input box stays visible during work and must not read as idle.
    from src.abort import claude_running, codex_running
    tail = '\n'.join(lines)
    if claude_running(tail) or codex_running(tail):
        return 'running', 'terminal shows a working spinner'
    if any(_prompt_is_empty(line) for line in raw_lines):
        return 'waiting_input', 'terminal shows an empty input prompt (heuristic)'
    return 'unknown', 'no reliable status indicator visible'


async def _transcript(pane, cwd, backend, pid):
    if backend == 'codex':
        return await backends.codex_transcript(pane, cwd)
    # Claude does not always hold its transcript open. Prefer the SessionStart
    # hook's pane tag (it follows /clear, unlike a stale --resume argument),
    # then an exact resume argument, then this live process's open transcript.
    tagged = await backends.claude_session(pane, cwd)
    if tagged and Path(tagged['transcript_path']).exists():
        return tagged['session_id'], tagged['transcript_path']
    try:
        args = procinfo.args(pid)
        sid = next((args[i + 1] for i, arg in enumerate(args[:-1])
                    if arg in {'--resume', '-r', '--session-id'}
                    and re.fullmatch(r'[a-f0-9-]{36}', args[i + 1])), None)
        if sid:
            for path in (Path.home() / '.claude/projects').glob(f'*/{sid}.jsonl'):
                return sid, str(path)
        out = await backends.command(procinfo.LSOF, '-a', '-p', pid, '-Fn')
        for line in out.splitlines():
            if line.startswith('n') and line.endswith('.jsonl') and '/.claude/projects/' in line:
                path = Path(line[1:])
                if re.fullmatch(r'[a-f0-9-]{36}', path.stem):
                    return path.stem, str(path)
    except (OSError, RuntimeError, TimeoutError):
        pass
    return None


async def discover():
    rows = await backends.command('tmux', 'list-panes', '-aF',
                                  '#{pane_id}\t#{pane_dead}\t#{pane_current_path}')
    sessions = []
    for row in rows.splitlines():
        pane, dead, cwd = row.split('\t', 2)
        if dead == '1':
            continue  # dead panes retain a tty name that may belong to a new pane
        for backend in ('codex', 'claude'):
            pids = await backends.pane_processes(pane, backend)
            if len(pids) != 1:
                continue  # do not guess which of several processes owns input
            pid = pids[0]
            # 启动标记防止回收的 PID 对上旧会话 ID；Linux / macOS 取法不同，见 procinfo
            start = procinfo.start_token(pid)
            actual_cwd = procinfo.cwd(pid)
            if not start or not actual_cwd:
                continue
            found = await _transcript(pane, actual_cwd, backend, pid)
            path = found[1] if found else None
            screen = await backends.command('tmux', 'capture-pane', '-e', '-p', '-t', pane)
            status, evidence = screen_state(screen)
            events = tail_events(path)
            summary, summary_role, model = '', None, ''
            for event in events:
                role, text = public_text(event)
                if text:
                    summary = ' '.join(text.split())[:240]
                    summary_role = role
                payload = event.get('payload', {})
                if event.get('type') == 'turn_context' or payload.get('type') == 'thread_settings_applied':
                    model = payload.get('model', model)
            from src import turn_status
            native_sid = found[0] if found else None
            if backend == 'codex' and native_sid:
                turn_status.bootstrap(native_sid, path)
            display_status, last_turn = turn_status.display_state(native_sid, status)
            sessions.append({
                'session_id': f'{backend}:{pane}:{pid}:{start}',
                'native_session_id': found[0] if found else None,
                'project': Path(actual_cwd).name, 'cwd': actual_cwd,
                'backend': backend, 'tmux_pane': pane, 'model': model,
                'status': status, 'status_evidence': evidence,
                'activity': display_status, 'last_turn': last_turn,
                'summary': summary or '暂无可确认的公开会话摘要',
                'summary_role': summary_role,
                'last_activity_at': Path(path).stat().st_mtime if path else None,
                'observed_at': time.time(), '_transcript': path,
            })
    return sessions


def receipt_seen(path, offset, marker):
    """Native receipt: a user message, or Claude's queued_command attachment for
    input that arrived mid-turn (inserted at the next tool call)."""
    for event in tail_events(path, offset):
        if event.get('type') == 'attachment':
            attachment = event.get('attachment') or {}
            if attachment.get('type') == 'queued_command' and marker in json.dumps(attachment, ensure_ascii=False):
                return True
            continue
        role, body = public_text(event)
        if role == 'user' and marker in body:
            return True
    return False


def visible(session):
    return {key: value for key, value in session.items() if not key.startswith('_')}


def recent_messages(path):
    latest = {'last_message': None, 'last_user_message': None, 'last_assistant_message': None}
    for event in tail_events(path):
        role, text = public_text(event)
        if not text:
            continue
        message = {'role': role, 'text': text[:4000], 'truncated': len(text) > 4000,
                   'timestamp': event.get('timestamp')}
        latest['last_message'] = message
        latest[f'last_{role}_message'] = message
    return latest


async def get_feedback(session_id, request_id=None):
    receipt = None
    if request_id:
        db = receipt_db()
        try:
            row = db.execute('SELECT result FROM receipts WHERE request_id=?', (request_id,)).fetchone()
            receipt = json.loads(row[0]) if row else None
        finally:
            db.close()
        if receipt and receipt['session_id'] != session_id:
            raise ValueError('request_id belongs to another session')
        if receipt and receipt['status'] == 'unconfirmed' and receipt.get('_marker'):
            if receipt_seen(receipt['_transcript'], receipt['_offset'], receipt['_marker']):
                receipt.update(status='received', detail='native user-message receipt observed after send returned')
                db = receipt_db()
                try:
                    db.execute('UPDATE receipts SET result=? WHERE request_id=?', (json.dumps(receipt), request_id))
                    db.commit()
                finally:
                    db.close()
    session = next((s for s in await discover() if s['session_id'] == session_id), None)
    result = {'session_id': session_id, 'alive': session is not None,
              'activity': session.get('activity', session['status']) if session else 'exited',
              'last_turn': session.get('last_turn', {}) if session else {},
              'observed_at': time.time(), 'delivery': visible(receipt) if receipt else None,
              'request_found': receipt is not None if request_id else None,
              'last_activity_at': session['last_activity_at'] if session else None,
              'activity_evidence': session['status_evidence'] if session else 'live process no longer found',
              'messages_available': bool(session and session['_transcript'])}
    result.update(recent_messages(session['_transcript'] if session else None))
    sent_at = (receipt or {}).get('sent_at')
    result['activity_after_send'] = bool(result['last_activity_at'] > sent_at) if sent_at and result['last_activity_at'] else None
    result['note'] = 'Recent output may concern another task; activity is not proof your request is complete.'
    return result


async def get_status(session_id):
    for session in await discover():
        if session['session_id'] == session_id:
            return visible(session)
    return {'session_id': session_id, 'status': 'exited',
            'status_evidence': 'not found in live terminal discovery', 'observed_at': time.time()}


def receipt_db():
    db = sqlite3.connect(RECEIPTS)
    RECEIPTS.chmod(0o600)
    db.execute('CREATE TABLE IF NOT EXISTS receipts '
               '(request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)')
    return db


async def resolve_sender(source_session_id, sender):
    if source_session_id:
        sessions = await discover()
        source = next((s for s in sessions if s['session_id'] == source_session_id), None)
        if not source:
            raise ValueError('sending session exited or changed; refresh the MCP connection')
        label = source['project']
        peers = [s for s in sessions if s['project'] == label]
        if len(peers) > 1:
            label += ':' + source['backend']
            if sum(s['backend'] == source['backend'] for s in peers) > 1:
                label += ':' + source['tmux_pane'].lstrip('%')
        # Project names are display data, not terminal syntax or trusted identity.
        label = re.sub(r'[\x00-\x1f\[\]]', '_', label)[:80]
        return label, source_session_id
    # Existing stdio processes keep their old code until their client reconnects.
    # Preserve delivery and receipt IDs during migration; never invent an origin.
    if not sender:
        sender = 'agent'
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', sender):
        raise ValueError('legacy sender must be a concrete session name')
    return sender, None


# Urgent messages may enter a working session: both CLIs insert input typed
# mid-turn at the next tool call. A confirmation prompt or unknown screen is
# still refused — typed text there could answer the prompt.
URGENT_STATES = {'waiting_input', 'running'}


async def send_message(session_id, text, request_id, sender=None, source_session_id=None, urgent=False):
    if not isinstance(text, str) or not text.strip() or len(text) > 16000:
        raise ValueError('text must contain 1–16000 characters')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id or ''):
        raise ValueError('request_id required (1–128 letters, digits, _, ., :, -)')
    sender, source_session_id = await resolve_sender(source_session_id, sender)
    if source_session_id == session_id:
        return {'request_id': request_id, 'session_id': session_id,
                'status': 'not_sent', 'detail': 'self-target forbidden', 'sender': sender}
    fingerprint = hashlib.sha256(json.dumps([session_id, text, sender] + ([source_session_id] if source_session_id else [])
                                            + (['urgent'] if urgent else [])).encode()).hexdigest()
    allowed = URGENT_STATES if urgent else {'waiting_input'}
    # Persist before any write. After a crash an uncertain request is never resent.
    db = receipt_db()
    try:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT fingerprint,result FROM receipts WHERE request_id=?', (request_id,)).fetchone()
        if row:
            if row[0] != fingerprint:
                raise ValueError('request_id already used with different content or target')
            return visible(json.loads(row[1]))
        result = {'request_id': request_id, 'session_id': session_id,
                  'sender': sender, 'source_session_id': source_session_id,
                  'status': 'unconfirmed', 'detail': 'reserved; do not automatically resend'}
        db.execute('INSERT INTO receipts VALUES (?,?,?)', (request_id, fingerprint, json.dumps(result)))
        db.commit()
    finally:
        db.close()
    async with _locks.setdefault(session_id, asyncio.Lock()):
        try:
            session = next((s for s in await discover() if s['session_id'] == session_id), None)
            if not session:
                result.update(status='not_sent', detail='session exited or changed; refresh list')
            elif session['status'] not in allowed:
                result.update(status='not_sent', detail=(
                    f"target is {session['status']} (only idle or working sessions accept urgent messages)" if urgent else
                    'target is not confirmed idle; inspect status and retry with a new request_id later'))
            elif not session['_transcript'] and session.get('backend') != 'codex':
                result.update(status='not_sent', detail='cannot locate transcript to confirm delivery')
            else:
                from src.bot import _inject_tmux, _project_locks
                from src.api_server import terminal_sessions
                cwd = next((s['cwd'] for s in terminal_sessions.values()
                            if s.get('tmux_pane') == session['tmux_pane']), session['cwd'])
                async with _project_locks.setdefault(cwd, asyncio.Lock()):
                    current = next((s for s in await discover() if s['session_id'] == session_id), None)
                    if not current or current['status'] not in allowed:
                        result.update(status='not_sent', detail='target changed or is no longer idle')
                    else:
                        path = current['_transcript']
                        # A fresh Codex thread created by /new has no rollout until its
                        # first user input.  It is still a valid, idle terminal target:
                        # snapshot all existing logs, inject once, then bind only the
                        # rollout that the live process opens for this message.
                        fresh_codex = current.get('backend') == 'codex' and not path
                        start_offsets = backends.codex_transcript_offsets() if fresh_codex else None
                        offset = Path(path).stat().st_size if path else 0
                        marker = f"[{'Urgent agent' if urgent else 'Agent'} message {request_id} from {sender}]"
                        result['sent_at'] = time.time()
                        result.update(_transcript=path, _offset=offset, _marker=marker)
                        db = receipt_db()
                        try:
                            db.execute('UPDATE receipts SET result=? WHERE request_id=?', (json.dumps(result), request_id))
                            db.commit()
                        finally:
                            db.close()
                        await _inject_tmux(current['tmux_pane'], marker + '\n' + text)
                        result.update(detail='written to terminal; native receipt not yet confirmed')
                        if urgent and current['status'] == 'running':
                            result.update(detail='written while target was working; it is inserted at the '
                                                 'next tool call — check get_session_feedback with this request_id')
                        for _ in range(40 if fresh_codex else 20):
                            if fresh_codex and not path:
                                found = await backends.codex_transcript(current['tmux_pane'], current['cwd'])
                                if found:
                                    _, path = found
                                    offset = start_offsets.get(str(Path(path).resolve()), 0)
                                    result.update(_transcript=path, _offset=offset)
                                    db = receipt_db()
                                    try:
                                        db.execute('UPDATE receipts SET result=? WHERE request_id=?',
                                                   (json.dumps(result), request_id))
                                        db.commit()
                                    finally:
                                        db.close()
                                    # Replace the stale pre-/new watcher as soon as the
                                    # first-turn rollout exists, so public replies are not lost.
                                    from src.api_server import register_pane_watcher
                                    await register_pane_watcher(current['tmux_pane'], current['cwd'],
                                                                start_offsets=start_offsets)
                            if receipt_seen(path, offset, marker):
                                result.update(status='received', detail='native user-message receipt observed; not a task completion')
                                break
                            await asyncio.sleep(.25)
        except Exception as exc:
            result.update(status='unconfirmed', detail=f'delivery interrupted ({type(exc).__name__}); do not automatically resend')
        db = receipt_db()
        try:
            db.execute('UPDATE receipts SET result=? WHERE request_id=?', (json.dumps(result), request_id))
            db.commit()
        finally:
            db.close()
    return visible(result)


async def handle_list(request):
    from aiohttp import web
    return web.json_response({'sessions': [visible(s) for s in await discover()]})


async def handle_status(request):
    from aiohttp import web
    return web.json_response(await get_status(request.match_info['id']))


async def handle_feedback(request):
    from aiohttp import web
    try:
        return web.json_response(await get_feedback(request.match_info['id'], request.query.get('request_id')))
    except ValueError as exc:
        return web.json_response({'error': str(exc)}, status=400)


async def handle_send(request):
    from aiohttp import web
    try:
        data = await request.json()
        result = await send_message(request.match_info['id'], data.get('text'),
                                    data.get('request_id'), data.get('sender'), data.get('source_session_id'),
                                    urgent=data.get('urgent') is True)
        return web.json_response(result)
    except (ValueError, TypeError, AttributeError) as exc:
        return web.json_response({'error': str(exc)}, status=400)
