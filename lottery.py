"""YC6 lottery 1, two accounts, ordinary Python only. No model integrations."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
from urllib import error, parse, request


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('redirect_refused')


def fetch_json(url, headers, body=None, method=None, missing_ok=False):
    headers = dict(headers)
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body).encode('utf-8')
    req = request.Request(url, headers=headers, data=data, method=method)
    try:
        with request.build_opener(NoRedirect()).open(req, timeout=20) as response:
            raw = response.read(2_000_001)
    except error.HTTPError as exc:
        if exc.code == 404 and missing_ok:
            return None
        # Never propagate server response text: it may embed credentials.
        raise RuntimeError(f'http_{exc.code}') from None
    if len(raw) > 2_000_000:
        raise RuntimeError('response_too_large')
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise RuntimeError('invalid_response')
    return result


class SiteAPI:
    def __init__(self, token):
        self.token = token

    def request(self, path, body=None):
        allowed = path in ('/me', '/lotteries/1', '/lotteries/1/join') or re.fullmatch(
            r'/me/lotteries\?page=[1-9][0-9]*&page_size=20', path)
        if not allowed or path.endswith('/join') != (body is not None):
            raise ValueError('out_of_scope_operation')
        headers = {'Accept': 'application/json', 'X-YCH-Public-Contract': '1',
                   'User-Agent': 'YC6TwoAccountScheduler/1.0', 'Origin': 'https://yc6.ai',
                   'Referer': 'https://yc6.ai/lottery.html?id=1'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        data = fetch_json('https://yc6.ai/api/v1' + path, headers, body)
        if data.get('success') is not True or not isinstance(data.get('data'), dict):
            raise RuntimeError('website_rejected_request')
        return data['data']


def identity(api):
    user_id = api.request('/me').get('user', {}).get('id')
    if isinstance(user_id, bool) or not str(user_id).isdigit() or int(user_id) <= 0:
        raise RuntimeError('invalid_account_identity')
    return int(user_id)


def join_current(api):
    data = api.request('/lotteries/1')
    item = data['item']
    if int(item.get('public_id', item.get('id', 0))) != 1:
        raise RuntimeError('wrong_activity')
    if item.get('my_entry', {}).get('joined') is True:
        return 'already_joined'
    if (item.get('drawn_at') or item.get('draw_state') != 'open'
            or item.get('is_visible') is not True or item.get('status') != 'active'
            or float(item['draw_at_ts']) <= float(data['server_time'])):
        return 'not_open'
    round_id = int(item['round_id'])
    if round_id <= 0:
        raise RuntimeError('invalid_round')
    # Do not retry POST blindly. Every later run rereads my_entry first.
    api.request('/lotteries/1/join', {'round_id': round_id})
    verified = api.request('/lotteries/1')['item']
    if int(verified['round_id']) != round_id or verified.get('my_entry', {}).get('joined') is not True:
        raise RuntimeError('participation_not_verified')
    return 'joined'


def own_wins(api):
    wins = set()
    for page in range(1, 1001):
        data = api.request(f'/me/lotteries?page={page}&page_size=20')
        if not isinstance(data.get('items'), list):
            raise RuntimeError('invalid_history')
        for item in data['items']:
            if 'public_id' not in item:
                raise RuntimeError('history_schema_changed')
            if str(item['public_id']) == '1' and item.get('result') == 'won':
                wins.add(int(item['id']))
        pagination = data.get('pagination', {})
        if int(pagination.get('page', 0)) != page:
            raise RuntimeError('invalid_pagination')
        pages = int(pagination.get('total_pages', 0))
        if pages < 0 or pages > 1000:
            raise RuntimeError('history_limit_exceeded')
        if pages <= page:
            return sorted(wins)
    raise RuntimeError('history_limit_exceeded')


def run_accounts(accounts, mode, make_api=SiteAPI):
    if mode not in ('verify', 'join', 'check'):
        raise ValueError('invalid_mode')
    records, authenticated = [], []
    for account in accounts:
        record = {'label': account['label'], 'identity_ok': False,
                  'participation': 'skipped', 'result_check': 'skipped', 'wins': [], 'errors': []}
        records.append(record)
        if not account['token']:
            record['errors'].append('missing_token')
            continue
        try:
            api = make_api(account['token'])
            user_id = identity(api)
            record['identity_ok'] = True
            authenticated.append((user_id, api, record))
        except Exception:
            record['errors'].append('authentication_failed')
    duplicate_ids = {uid for uid, _, _ in authenticated
                     if sum(other == uid for other, _, _ in authenticated) > 1}
    for user_id, api, record in authenticated:
        if user_id in duplicate_ids:
            record['errors'].append('duplicate_account')
            continue
        if mode == 'verify':
            continue
        if mode == 'join':
            try:
                record['participation'] = join_current(api)
            except Exception:
                record['participation'] = 'failed'
                record['errors'].append('participation_failed')
        # A failed signup must never suppress the independent result query.
        try:
            record['wins'] = own_wins(api)
            record['result_check'] = 'ok'
        except Exception:
            record['result_check'] = 'failed'
            record['errors'].append('result_check_failed')
    return records


class GitHubState:
    """Only timestamps and a Boolean reservation ever enter repository state."""
    def __init__(self, repository, token):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or not token:
            raise RuntimeError('missing_github_state_credentials')
        self.base = 'https://api.github.com/repos/' + repository
        self.headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'YC6TwoAccountScheduler/1.0'}

    def load(self, name):
        result = fetch_json(self.base + '/contents/.state/' + name, self.headers, missing_ok=True)
        if result is None:
            return None, None
        content = json.loads(base64.b64decode(result['content'], validate=False))
        if not isinstance(content, dict):
            raise RuntimeError('invalid_repository_state')
        return content, result['sha']

    def write(self, name, value, sha):
        content = base64.b64encode(json.dumps(value, sort_keys=True).encode()).decode()
        body = {'message': 'chore: persist lottery automation state [skip ci]', 'content': content}
        if sha:
            body['sha'] = sha
        # GitHub's blob SHA provides compare-and-swap. Never retry an ambiguous write.
        fetch_json(self.base + '/contents/.state/' + name, self.headers, body, method='PUT')

    def reserve(self):
        content, sha = self.load('notification.json')
        if content is not None:
            if content.get('reserved') is not True:
                raise RuntimeError('unexpected_notification_state')
            return False
        self.write('notification.json', {'reserved': True, 'at': datetime.now(timezone.utc).isoformat()}, sha)
        return True

    def heartbeat(self):
        content, sha = self.load('heartbeat.json')
        now = datetime.now(timezone.utc)
        if content is not None and now - datetime.fromisoformat(content['at']) < timedelta(days=28):
            return
        self.write('heartbeat.json', {'at': now.isoformat()}, sha)


def notify_once(records, store, send, enabled):
    winners = [record['label'] for record in records if record['wins']]
    if not winners:
        return 'no_win'
    if not enabled:
        return 'channel_not_configured'
    # Reservation precedes send: at most one attempt total, including across
    # accounts, restarts, repeated wins and ambiguous network failures.
    try:
        if not store.reserve():
            return 'already_reserved'
    except Exception:
        return 'reservation_failed'
    message = 'YC6 抽奖已确认中奖：' + '、'.join(winners) + '。请在对应账号查看奖品：\nhttps://yc6.ai/account.html?view=lotteries'
    try:
        send(message)
    except Exception:
        return 'delivery_uncertain'
    return 'sent'


def telegram_sender(token, chat_id):
    def send(message):
        result = fetch_json('https://api.telegram.org/bot' + token + '/sendMessage',
                            {'Accept': 'application/json'}, {'chat_id': chat_id, 'text': message}, method='POST')
        if result.get('ok') is not True:
            raise RuntimeError('telegram_rejected_message')
    return send


def summary(records, notification):
    rows = ['## YC6 双账号运行结果', '', '| 账号 | 登录验证 | 报名 | 本人结果查询 | 已中奖期数 | 状态 |',
            '|---|---|---|---|---|---|']
    for record in records:
        rows.append('| {label} | {auth} | {participation} | {result_check} | {count} | {errors} |'.format(
            **{k: v for k, v in record.items() if k != 'errors'},
            auth='ok' if record['identity_ok'] else 'failed', count=len(record['wins']),
            errors=', '.join(record['errors']) or 'ok'))
    rows.extend(['', '通知状态：' + notification, '',
                 '本程序未调用 GPT。missing_token 表示尚未接入账号；authentication_failed 表示登录状态无效。',
                 '无论是否中奖，账号资料、token 和奖品卡密均不写入日志或仓库。'])
    value = '\n'.join(rows) + '\n'
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if path:
        with open(path, 'a', encoding='utf-8') as stream:
            stream.write(value)
    print(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('verify', 'join', 'check'), default='verify')
    parser.add_argument('--probe-public', action='store_true')
    args = parser.parse_args()
    if args.probe_public:
        item = SiteAPI('').request('/lotteries/1')['item']
        print(json.dumps({key: item.get(key) for key in ('public_id', 'round_id', 'draw_state', 'daily_draw_time')}))
        return 0
    accounts = [{'label': label, 'token': os.environ.get('YC6_TOKEN_' + label.upper(), '').strip()}
                for label in ('Edge', 'Chrome')]
    records = run_accounts(accounts, args.mode)
    notification = 'verify_only'
    state_failed = False
    if args.mode != 'verify':
        bot = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
        chat = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
        try:
            state = GitHubState(os.environ.get('GITHUB_REPOSITORY', ''), os.environ.get('GITHUB_TOKEN', ''))
            notification = notify_once(records, state, telegram_sender(bot, chat), bool(bot and chat))
            state.heartbeat()
        except Exception:
            state_failed = True
            notification = 'repository_state_failed'
    summary(records, notification)
    return int(any(record['errors'] for record in records) or state_failed
               or notification in ('reservation_failed', 'delivery_uncertain'))


if __name__ == '__main__':
    # Raw exception details can contain credentials; report only safe outcomes.
    try:
        sys.exit(main())
    except Exception:
        print('Automation stopped: configuration or network error. No raw exception details logged.', file=sys.stderr)
        sys.exit(1)
