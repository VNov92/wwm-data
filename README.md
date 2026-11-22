# wwm-data — Bộ thu thập dữ liệu tự phục hồi cho Where Winds Meet

Kho dữ liệu tĩnh và bộ thu thập tự động (self-healing crawler) phục vụ cập nhật **Giftcode** và **Ghi chú bản vá (Patch Notes)** tiếng Việt cho ứng dụng Where Winds Meet.

Dữ liệu đầu ra được lưu tại `data/codes.json` và `data/patch.json` theo định dạng `schema_version: 1`, tự động cập nhật định kỳ mỗi 6 giờ qua GitHub Actions.

---

## Cơ chế tự phục hồi 4 tầng (Self-healing Tiers)

Mỗi nguồn dữ liệu chạy tuần tự các tầng từ nhẹ đến nặng, dừng ngay tại tầng đầu tiên trả về dữ liệu hợp lệ:

### 1. Giftcode (`codes.yar.gg`)
1. **`api`**: Gọi trực tiếp các endpoint JSON (`codes.yar.gg/api/codes` và worker dự phòng) để lấy danh sách `active` và `expired`.
2. **`html`**: Tải HTML bằng `Fetcher` (impersonate Chrome) và trích xuất thẻ `[data-code]` với **Scrapling adaptive selector** (`data/adaptive.db` lưu dấu vết DOM để tự thích ứng khi giao diện thay đổi).
3. **`dynamic`**: Khi bị chặn JS/challenge hoặc cần render động, dùng trình duyệt không đầu `StealthyFetcher` (Patchright Chromium) chờ `network_idle` và `[data-code]`.
4. **`text`**: Trích xuất mã quà tặng từ văn bản hiển thị bằng regex gần từ khóa `code`, hoặc gọi **Gemini** (`gemini-3.5-flash-lite` → `gemini-3.1-flash-lite`) nếu có `GEMINI_API_KEY`.

### 2. Patch Notes (`wherewindsmeetgame.com` + Steam)
1. **`html`**: Quét trang tin chính thức `wherewindsmeetgame.com/news/`, tìm bài viết patch notes mới nhất (kèm adaptive selector).
2. **`dynamic`**: Fallback sang `StealthyFetcher` khi trang chính thức yêu cầu render JS hoặc gặp challenge.
3. **`steam`**: Fallback sang Steam News API (`ISteamNews/GetNewsForApp`, App ID `3564740`) nếu trang chính thức không truy cập được.

Nếu phát hiện URL bài viết mới chưa có trong `patch.sources`, crawler dùng Gemini dịch và chuẩn hóa nội dung sang tiếng Việt; nếu `GEMINI_API_KEY` thiếu hoặc kiểm định thất bại, dữ liệu cũ được giữ nguyên an toàn.

---

## Cấu trúc `data/*.json` (`schema_version: 1`)

### Khối `health` (có trong cả hai file)
```json
{
  "status": "ok | degraded | failing",
  "last_attempt_at": "2026-11-22T09:00:00Z",
  "last_success_at": "2026-11-22T09:00:00Z",
  "sources": [
    { "name": "codes.yar.gg", "tier": "api", "result": "ok", "message": "ok" }
  ]
}
```
- **`ok`**: Lần chạy gần nhất thành công và vượt qua bộ kiểm định (`validate_codes` / `validate_patch`).
- **`degraded`**: Lần gần nhất lỗi nhưng lần thành công gần nhất vẫn trong vòng `24` giờ (`STALE_HOURS`).
- **`failing`**: Chưa từng thành công hoặc đã quá `24` giờ chưa cập nhật được.

### `data/codes.json`
- `schema_version`: `1`
- `health`: Trạng thái thu thập giftcode.
- `items`: Danh sách object `{ "code": string, "reward_text": string | null, "added_at": string | null }`.
- `expired`: Danh sách chuỗi mã đã hết hạn.
- Bộ kiểm định từ chối cập nhật nếu danh sách rỗng hoặc số mã còn hiệu lực sụt giảm dưới `50%` (`SHRINK_RATIO = 0.5`).

### `data/patch.json`
- `schema_version`: `1`
- `health`: Trạng thái thu thập và dịch patch notes.
- `patch`: `{ version, releaseDate, title, summary, highlights, sections, sources }` (yêu cầu ít nhất 1 nguồn `https` thuộc `wherewindsmeetgame.com` hoặc `store.steampowered.com` và không tụt phiên bản).

---

## Chạy thử trên máy cục bộ (Local)

```powershell
# 1. Cài đặt thư viện và trình duyệt cho Scrapling / Patchright
pip install -r requirements.txt
scrapling install
python -m patchright install chromium

# 2. Chạy toàn bộ unit test
.venv\Scripts\python -m pytest -q

# 3. Chạy crawler (tùy chọn đặt GEMINI_API_KEY để dịch patch notes mới)
$env:GEMINI_API_KEY="your-gemini-api-key"
.venv\Scripts\python -m crawler.main
```

Lưu ý: Tệp `data/adaptive.db*` dùng cho bộ nhớ thích ứng cục bộ và đã được loại trừ trong `.gitignore`.

---

## Thiết lập GitHub & Tự động hóa

Workflow `.github/workflows/crawl.yml` chạy theo lịch `cron: "0 */6 * * *"` (mỗi 6 giờ) hoặc kích hoạt thủ công qua `workflow_dispatch`, tự động commit & push các thay đổi trong `data/` dưới tên `github-actions[bot]`.

**Các bước thiết lập thủ công lần đầu trên GitHub:**
1. Tạo repository public **`VNov92/wwm-data`** trên GitHub.
2. Vào **Settings → Secrets and variables → Actions**, thêm Repository Secret tên **`GEMINI_API_KEY`**.
3. Push nhánh local lên `origin/main` và kiểm tra tab **Actions** bằng nút **Run workflow**.
