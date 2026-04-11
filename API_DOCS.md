# 📡 Netflix Cookie API – Tài liệu hướng dẫn

> **Base URL:** `https://nft-netflix.onrender.com`  
> **Phiên bản:** 1.0  
> **Cập nhật:** 11/04/2026

---

## Mục lục

1. [Tổng quan](#1-tổng-quan)
2. [Danh sách cookie – GET /api/public/list](#2-danh-sách-cookie)
3. [Lấy link đăng nhập – POST /api/public/get-login-link](#3-lấy-link-đăng-nhập)
4. [Kiểm tra cookie – POST /api/public/check](#4-kiểm-tra-cookie)
5. [Đăng nhập TV – POST /api/public/tv-login](#5-đăng-nhập-tv)
6. [Mã lỗi](#6-mã-lỗi)
7. [Ví dụ tích hợp](#7-ví-dụ-tích-hợp)

---

## 1. Tổng quan

API cho phép website bên ngoài:
- Truy xuất danh sách cookie Netflix đã lưu trong kho Supabase
- Tạo link đăng nhập tự động (nftoken) từ cookie đã lưu
- Kiểm tra cookie còn hợp lệ (LIVE) hay đã hết hạn (DEAD)
- Đăng nhập Netflix trên Smart TV bằng mã code

### Luồng sử dụng

```
Bước 1                    Bước 2                    Bước 3
GET /api/public/list  →   Chọn 1 cookie (lấy ID) →  POST check (kiểm tra)
                                                     POST get-login-link
                                                     POST tv-login
```

### Lưu ý bảo mật

- Cookie text gốc **không bao giờ** được trả ra ở `/api/public/list`
- Chỉ trả metadata: email, gói cước, quốc gia, link đăng nhập...
- Tất cả request đều sử dụng `Content-Type: application/json`

### Tài liệu trực tuyến

Truy cập trang tài liệu trực tuyến tại:
```
https://nft-netflix.onrender.com/api/docs
```

---

## 2. Danh sách cookie

Lấy toàn bộ danh sách cookie đã lưu trong kho. Sắp xếp theo thời gian (mới nhất trước).

```
GET /api/public/list
```

### Request

Không cần tham số.

### Response `200 OK`

```json
[
  {
    "id": "3aa4efa7-f20b-4973-8f3a-b9ffb60c308e",
    "savedAt": "2026-04-11T07:53:03.797Z",
    "email": "user@gmail.com",
    "owner": "Nguyễn Văn A",
    "plan": "Premium",
    "country": "US",
    "billing": "15/05/2026",
    "profiles": "User1, User2, Kids",
    "numProfiles": 3,
    "videoQuality": "Ultra HD",
    "maxStreams": "4",
    "login_link": "https://netflix.com/?nftoken=abc123...",
    "nftoken": "abc123...",
    "label": ""
  }
]
```

### Bảng mô tả trường

| Trường          | Kiểu      | Mô tả                                      |
|-----------------|-----------|---------------------------------------------|
| `id`            | `uuid`    | ID duy nhất, dùng cho các API khác          |
| `savedAt`       | `string`  | Thời gian lưu (ISO 8601)                    |
| `email`         | `string`  | Email tài khoản Netflix                     |
| `owner`         | `string`  | Tên chủ tài khoản                           |
| `plan`          | `string`  | Gói cước (Standard, Premium...)             |
| `country`       | `string`  | Mã quốc gia (US, FR, VN...)                 |
| `billing`       | `string`  | Ngày gia hạn tiếp theo                      |
| `profiles`      | `string`  | Danh sách hồ sơ, cách nhau bởi dấu phẩy    |
| `numProfiles`   | `integer` | Số lượng hồ sơ                              |
| `videoQuality`  | `string`  | Chất lượng video (HD, Ultra HD...)           |
| `maxStreams`     | `string`  | Số luồng phát tối đa                        |
| `login_link`    | `string`  | Link đăng nhập tự động (có thể cũ/hết hạn)  |
| `nftoken`       | `string`  | Mã token đăng nhập                          |
| `label`         | `string`  | Nhãn tùy chỉnh                              |

### Ví dụ

**cURL:**
```bash
curl "https://nft-netflix.onrender.com/api/public/list"
```

**JavaScript:**
```javascript
const res = await fetch('https://nft-netflix.onrender.com/api/public/list');
const cookies = await res.json();

cookies.forEach(item => {
  console.log(item.id, item.email, item.plan);
});
```

**Python:**
```python
import requests

res = requests.get("https://nft-netflix.onrender.com/api/public/list")
cookies = res.json()

for item in cookies:
    print(item["id"], item["email"], item["plan"])
```

---

## 3. Lấy link đăng nhập

Tạo link đăng nhập Netflix tự động từ cookie đã lưu. Link mới sẽ được cập nhật lại vào kho lưu trữ.

```
POST /api/public/get-login-link
```

### Request Body

| Tham số | Kiểu           | Bắt buộc | Mô tả                                     |
|---------|----------------|----------|--------------------------------------------|
| `id`    | `string (uuid)` | ✅ Có    | ID cookie lấy từ `/api/public/list`        |

```json
{
  "id": "3aa4efa7-f20b-4973-8f3a-b9ffb60c308e"
}
```

### Response thành công `200 OK`

```json
{
  "status": "SUCCESS",
  "login_link": "https://netflix.com/?nftoken=abc123...",
  "nftoken": "abc123...",
  "account": {
    "email": "user@gmail.com",
    "plan": "Premium",
    "country": "US",
    "owner": "Nguyễn Văn A",
    "profiles": "User1, User2",
    "numProfiles": 2
  }
}
```

### Response thất bại

```json
{
  "status": "ERROR",
  "error": "Cookie hết hạn hoặc không hợp lệ"
}
```

### Ví dụ

**cURL:**
```bash
curl -X POST "https://nft-netflix.onrender.com/api/public/get-login-link" \
  -H "Content-Type: application/json" \
  -d '{"id": "3aa4efa7-f20b-4973-8f3a-b9ffb60c308e"}'
```

**JavaScript:**
```javascript
const res = await fetch('https://nft-netflix.onrender.com/api/public/get-login-link', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ id: '3aa4efa7-f20b-4973-8f3a-b9ffb60c308e' })
});
const data = await res.json();

if (data.status === 'SUCCESS') {
  console.log('Link:', data.login_link);
  // Mở link để đăng nhập tự động
  window.open(data.login_link);
}
```

**Python:**
```python
import requests

res = requests.post(
    "https://nft-netflix.onrender.com/api/public/get-login-link",
    json={"id": "3aa4efa7-f20b-4973-8f3a-b9ffb60c308e"}
)
data = res.json()

if data["status"] == "SUCCESS":
    print("Link đăng nhập:", data["login_link"])
```

---

## 4. Kiểm tra cookie

Kiểm tra xem cookie đã lưu trong kho còn hợp lệ hay đã hết hạn. Không tạo token (nhanh hơn get-login-link).

```
POST /api/public/check
```

### Request Body

| Tham số | Kiểu           | Bắt buộc | Mô tả                                     |
|---------|----------------|----------|--------------------------------------------|
| `id`    | `string (uuid)` | ✅ Có    | ID cookie lấy từ `/api/public/list`        |

```json
{
  "id": "3aa4efa7-f20b-4973-8f3a-b9ffb60c308e"
}
```

### Response – Cookie hợp lệ `200 OK`

```json
{
  "status": "LIVE",
  "account": {
    "email": "user@gmail.com",
    "plan": "Premium",
    "country": "US",
    "owner": "Nguyễn Văn A",
    "profiles": "User1, User2",
    "numProfiles": 2,
    "billing": "15/05/2026",
    "videoQuality": "Ultra HD",
    "maxStreams": "4",
    "memberSince": "5 January 2024"
  }
}
```

### Response – Cookie hết hạn `200 OK`

```json
{
  "status": "DEAD"
}
```

### Response – Lỗi

```json
{
  "status": "ERROR",
  "error": "Không tìm thấy cookie trong kho"
}
```

### Ví dụ

**cURL:**
```bash
curl -X POST "https://nft-netflix.onrender.com/api/public/check" \
  -H "Content-Type: application/json" \
  -d '{"id": "3aa4efa7-f20b-4973-8f3a-b9ffb60c308e"}'
```

**JavaScript:**
```javascript
const res = await fetch('https://nft-netflix.onrender.com/api/public/check', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ id: '3aa4efa7-f20b-4973-8f3a-b9ffb60c308e' })
});
const data = await res.json();

if (data.status === 'LIVE') {
  console.log('✅ Cookie hợp lệ!', data.account.plan, data.account.country);
} else if (data.status === 'DEAD') {
  console.log('❌ Cookie đã hết hạn');
} else {
  console.log('⚠️ Lỗi:', data.error);
}
```

**Python:**
```python
import requests

res = requests.post(
    "https://nft-netflix.onrender.com/api/public/check",
    json={"id": "3aa4efa7-f20b-4973-8f3a-b9ffb60c308e"}
)
data = res.json()

if data["status"] == "LIVE":
    acc = data["account"]
    print(f"✅ Hợp lệ: {acc['email']} - {acc['plan']} ({acc['country']})")
elif data["status"] == "DEAD":
    print("❌ Cookie đã hết hạn")
else:
    print("⚠️ Lỗi:", data.get("error"))
```

---

## 5. Đăng nhập TV

Đăng nhập Netflix trên Smart TV / thiết bị streaming bằng mã code hiển thị trên TV.

```
POST /api/public/tv-login
```

### Request Body

| Tham số   | Kiểu           | Bắt buộc | Mô tả                                          |
|-----------|----------------|----------|-------------------------------------------------|
| `id`      | `string (uuid)` | ✅ Có    | ID cookie trong kho lưu trữ                    |
| `tv_code` | `string`        | ✅ Có    | Mã code 8 ký tự trên màn hình TV (VD: ABCD1234)|

```json
{
  "id": "3aa4efa7-f20b-4973-8f3a-b9ffb60c308e",
  "tv_code": "ABCD1234"
}
```

### Response thành công `200 OK`

```json
{
  "status": "SUCCESS",
  "message": "Đăng nhập TV thành công!",
  "account": {
    "email": "user@gmail.com",
    "plan": "Premium",
    "country": "US",
    "owner": "Nguyễn Văn A"
  }
}
```

### Response thất bại

```json
// Cookie hết hạn
{
  "status": "ERROR",
  "error": "Cookie DEAD."
}

// Thiếu authURL
{
  "status": "ERROR",
  "error": "Không tìm thấy authURL. Cookie cần có SecureNetflixId và nfvdid."
}

// Mã TV sai hoặc hết hạn
{
  "status": "FAILED",
  "message": "Mã code không hợp lệ hoặc đã hết hạn"
}
```

### Ví dụ

**cURL:**
```bash
curl -X POST "https://nft-netflix.onrender.com/api/public/tv-login" \
  -H "Content-Type: application/json" \
  -d '{"id": "3aa4efa7-...", "tv_code": "ABCD1234"}'
```

**JavaScript:**
```javascript
const res = await fetch('https://nft-netflix.onrender.com/api/public/tv-login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    id: '3aa4efa7-f20b-4973-8f3a-b9ffb60c308e',
    tv_code: 'ABCD1234'
  })
});
const data = await res.json();

if (data.status === 'SUCCESS') {
  alert('✅ ' + data.message);
} else {
  alert('❌ ' + (data.error || data.message));
}
```

**Python:**
```python
import requests

res = requests.post(
    "https://nft-netflix.onrender.com/api/public/tv-login",
    json={
        "id": "3aa4efa7-f20b-4973-8f3a-b9ffb60c308e",
        "tv_code": "ABCD1234"
    }
)
data = res.json()

if data["status"] == "SUCCESS":
    print("✅", data["message"])
else:
    print("❌", data.get("error", data.get("message")))
```

---

## 6. Mã lỗi

| Status      | Ý nghĩa                                              |
|-------------|-------------------------------------------------------|
| `LIVE`      | Cookie còn hợp lệ (chỉ dùng ở endpoint check)        |
| `DEAD`      | Cookie đã hết hạn (chỉ dùng ở endpoint check)        |
| `SUCCESS`   | Thao tác thành công                                   |
| `ERROR`     | Lỗi phía server hoặc cookie không hợp lệ             |
| `FAILED`    | Thao tác TV login thất bại (mã sai, hết hạn...)      |

| HTTP Code | Ý nghĩa                                               |
|-----------|--------------------------------------------------------|
| `200`     | Thành công                                             |
| `400`     | Thiếu tham số bắt buộc                                |
| `404`     | Không tìm thấy cookie với ID được cung cấp            |
| `500`     | Lỗi kết nối kho lưu trữ Supabase                     |

---

## 7. Ví dụ tích hợp

### Tích hợp vào website HTML/JS

```html
<!DOCTYPE html>
<html>
<head>
  <title>Netflix Login</title>
</head>
<body>
  <h1>Chọn tài khoản Netflix</h1>

  <select id="account-select">
    <option value="">-- Chọn tài khoản --</option>
  </select>

  <button onclick="getLink()">Lấy Link Đăng Nhập</button>

  <div>
    <input id="tv-code" placeholder="Nhập mã TV (8 ký tự)">
    <button onclick="tvLogin()">Đăng nhập TV</button>
  </div>

  <div id="result"></div>

  <script>
    const API = 'https://nft-netflix.onrender.com';

    // Bước 1: Load danh sách tài khoản
    async function loadAccounts() {
      const res = await fetch(`${API}/api/public/list`);
      const list = await res.json();

      const sel = document.getElementById('account-select');
      list.forEach(item => {
        const opt = document.createElement('option');
        opt.value = item.id;
        opt.textContent = `${item.email} — ${item.plan} (${item.country})`;
        sel.appendChild(opt);
      });
    }

    // Bước 2a: Lấy link đăng nhập
    async function getLink() {
      const id = document.getElementById('account-select').value;
      if (!id) return alert('Chọn tài khoản trước');

      const res = await fetch(`${API}/api/public/get-login-link`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id })
      });
      const data = await res.json();

      if (data.status === 'SUCCESS') {
        document.getElementById('result').innerHTML =
          `<a href="${data.login_link}" target="_blank">Mở Netflix →</a>
           <br>Gói: ${data.account.plan} | Quốc gia: ${data.account.country}`;
      } else {
        document.getElementById('result').textContent = '❌ ' + data.error;
      }
    }

    // Bước 2b: Đăng nhập TV
    async function tvLogin() {
      const id = document.getElementById('account-select').value;
      const code = document.getElementById('tv-code').value.trim();
      if (!id || !code) return alert('Chọn tài khoản và nhập mã TV');

      const res = await fetch(`${API}/api/public/tv-login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, tv_code: code })
      });
      const data = await res.json();

      document.getElementById('result').textContent =
        data.status === 'SUCCESS'
          ? '✅ ' + data.message
          : '❌ ' + (data.error || data.message);
    }

    loadAccounts();
  </script>
</body>
</html>
```

---

> **Ghi chú:** Nếu server đang tắt hoặc Render đang khởi động lại (cold start), 
> request đầu tiên có thể mất 15–30 giây. Các request sau sẽ nhanh bình thường.
