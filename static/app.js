(function() {
    'use strict';

    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    // ────────────────────────────────────────────────────────────────
    // UI Helpers
    // ────────────────────────────────────────────────────────────────
    function toast(msg, type = 'info') {
        const el = document.createElement('div');
        el.className = 'toast toast-' + type;
        el.textContent = msg;
        $('#toast-container').appendChild(el);
        requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('show')));
        setTimeout(() => {
            el.classList.remove('show');
            setTimeout(() => el.remove(), 300);
        }, 3000);
    }

    function esc(s) {
        if (!s || s === '-') return s || '-';
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    function copyToClipboard(text) {
        if (!text || text === '-') {
            toast('Không có nội dung để sao chép', 'error');
            return;
        }
        navigator.clipboard.writeText(text).then(
            () => toast('Đã sao chép vào khay nhớ tạm', 'success'),
            () => toast('Lỗi sao chép', 'error')
        );
    }

    function setLoading(btnId, isLoad) {
        const btn = $(btnId);
        if (!btn) return;
        const span = btn.querySelector('span');
        const spin = btn.querySelector('.btn-spinner');
        if (isLoad) {
            btn.disabled = true;
            if(span) span.classList.add('hidden');
            if(spin) spin.classList.remove('hidden');
        } else {
            btn.disabled = false;
            if(span) span.classList.remove('hidden');
            if(spin) spin.classList.add('hidden');
        }
    }

    // ────────────────────────────────────────────────────────────────
    // Navigation
    // ────────────────────────────────────────────────────────────────
    $$('.nav-links li[data-tab]').forEach(tab => {
        tab.addEventListener('click', () => {
            const targetId = 'panel-' + tab.dataset.tab;
            
            $$('.nav-links li').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            $$('.panel').forEach(p => {
                if (p.id !== targetId) {
                    p.classList.remove('active');
                    p.classList.add('hidden'); // Immediately hide others (or use animation)
                }
            });

            const target = $('#' + targetId);
            target.classList.remove('hidden');
            // Small delay to ensure browser registers the removal of 'hidden' before applying 'active' animation
            requestAnimationFrame(() => {
                target.classList.add('active');
            });
        });
    });

    // ────────────────────────────────────────────────────────────────
    // Single Check
    // ────────────────────────────────────────────────────────────────
    const sCookie = $('#single-cookie');
    const sBtnFile = $('#btn-upload-single');
    const sFileInput = $('#single-file-input');
    const sBtnPaste = $('#btn-paste-single');
    const sBtnClear = $('#btn-clear-single');
    const sBtnCheck = $('#btn-check-single');
    
    // Modes
    const mFull = $('#mode-full');
    const mToken = $('#mode-token');
    
    mFull.addEventListener('click', () => { mFull.classList.add('active'); mToken.classList.remove('active'); });
    mToken.addEventListener('click', () => { mToken.classList.add('active'); mFull.classList.remove('active'); });

    // Actions
    sBtnClear.addEventListener('click', () => { sCookie.value = ''; sCookie.focus(); });
    sBtnPaste.addEventListener('click', async () => {
        try {
            const txt = await navigator.clipboard.readText();
            sCookie.value = txt;
        } catch(e) { toast('Không thể dán từ clipboard', 'error'); }
    });
    sBtnFile.addEventListener('click', () => sFileInput.click());
    sFileInput.addEventListener('change', (e) => {
        const f = e.target.files[0];
        if (!f) return;
        const r = new FileReader();
        r.onload = e => { sCookie.value = e.target.result; sFileInput.value = ''; };
        r.readAsText(f);
    });

    // Submitting
    sBtnCheck.addEventListener('click', async () => {
        const val = sCookie.value.trim();
        if (!val) { toast('Vui lòng nhập cookie', 'error'); return; }

        setLoading('#btn-check-single', true);
        $('#single-empty').classList.add('hidden');
        $('#single-result-container').classList.add('hidden');
        $('#single-error-container').classList.add('hidden');
        $('#btn-copy-result').style.display = 'none';

        // Fake progress
        $('#single-prog-lbl').textContent = 'Đang xử lý...';
        $('#single-prog-pct').textContent = '45%';
        $('#single-prog-fill').style.width = '45%';

        try {
            const res = await fetch('/api/check-single', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ cookie: val })
            });
            const data = await res.json();
            
            $('#single-prog-pct').textContent = '100%';
            $('#single-prog-fill').style.width = '100%';
            $('#single-prog-lbl').textContent = 'Xong';

            if (data.status === 'LIVE') {
                populateSingleLive(data);
                $('#single-result-container').classList.remove('hidden');
                $('#btn-copy-result').style.display = 'block';
                $('#btn-save-single').style.display = 'block';
                $('#btn-copy-result').onclick = () => copyToClipboard(buildTextCopy(data));
                
                window.lastSingleCookieText = val;
                window.lastSingleData = data;
                $('#btn-save-single').onclick = async () => {
                    setLoading('#btn-save-single', true);
                    const btn = $('#btn-save-single');
                    const oldText = btn.textContent;
                    btn.textContent = 'ĐANG LƯU...';
                    await autoSaveCookie(window.lastSingleCookieText, window.lastSingleData);
                    toast('Đã lưu cookie hợp lệ vào kho', 'success');
                    btn.textContent = oldText;
                    btn.style.display = 'none'; // hide after save
                };
            } else {
                $('#single-error-msg').textContent = data.error || 'Cookie đã hết hạn hoặc không hợp lệ.';
                $('#single-error-container').classList.remove('hidden');
            }
        } catch (e) {
            $('#single-error-msg').textContent = 'Lỗi kết nối máy chủ: ' + e.message;
            $('#single-error-container').classList.remove('hidden');
        } finally {
            setLoading('#btn-check-single', false);
        }
    });

    // Auto-save valid cookie to storage
    async function autoSaveCookie(cookieText, resultData) {
        try {
            await fetch('/api/storage/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    cookie: cookieText,
                    plan: resultData.plan,
                    country: resultData.country,
                    email: resultData.email,
                    owner: resultData.owner,
                    profiles: resultData.profiles,
                    numProfiles: resultData.numProfiles,
                    billing: resultData.billing,
                    login_link: resultData.login_link || '',
                    nftoken: resultData.nftoken || '',
                    videoQuality: resultData.videoQuality,
                    maxStreams: resultData.maxStreams,
                })
            });
        } catch(e) {
            // Silent fail - don't interrupt user
        }
    }

    function populateSingleLive(r) {
        const isFocusToken = mToken.classList.contains('active');
        
        if (isFocusToken) {
            $('#single-info-grid').style.opacity = '0.5';
        } else {
            $('#single-info-grid').style.opacity = '1';
        }

        const qual = r.videoQuality && r.videoQuality !== '-' ? ` (${r.videoQuality})` : '';
        const profs = r.numProfiles + (r.profiles && r.profiles !== '-' ? ` — ${r.profiles}` : '');

        $('#r-plan').textContent = esc(r.plan) + qual;
        $('#r-country').textContent = esc(r.country) + ` (${esc(r.currency)})`;
        $('#r-video').textContent = esc(r.maxStreams);
        $('#r-email').textContent = esc(r.email);
        $('#r-billing').textContent = esc(r.billing);
        $('#r-payment').textContent = esc(r.paymentType) + ' ' + esc(r.last4);
        $('#r-profiles').textContent = esc(profs);

        if (r.login_link) {
            $('#r-expire').textContent = 'Tự động (theo NFToken)';
            $('#r-link').textContent = r.login_link;
            $('#r-token').textContent = r.nftoken;
        } else {
            $('#r-expire').textContent = '-';
            $('#r-link').innerHTML = '<span class="error-text">Không tạo được link đăng nhập.</span>';
            $('#r-token').textContent = '-';
        }
    }

    function buildTextCopy(r) {
        if (!r.login_link) return `[${r.country}] ${r.plan} - ${r.email}`;
        return `LIVE | ${r.login_link} | Plan=${r.plan} | Country=${r.country} | Email=${r.email}`;
    }

    $('#btn-copy-link').addEventListener('click', () => copyToClipboard($('#r-link').textContent));
    $('#btn-copy-token').addEventListener('click', () => copyToClipboard($('#r-token').textContent));


    // ────────────────────────────────────────────────────────────────
    // Bulk Check
    // ────────────────────────────────────────────────────────────────
    let bulkFiles = [];
    let bulkResults = [];

    const bUploadZone = $('#bulk-upload-zone');
    const bFileInput = $('#bulk-file-input');
    const bSelect = $('#bulk-file-select');
    const bCount = $('#bulk-file-count');
    const btnClearBulk = $('#btn-clear-bulk');
    const bBtnRun = $('#btn-run-bulk');

    bUploadZone.addEventListener('click', () => bFileInput.click());
    bUploadZone.addEventListener('dragover', e => { e.preventDefault(); bUploadZone.style.borderColor = '#c4080f'; });
    bUploadZone.addEventListener('dragleave', () => { bUploadZone.style.borderColor = 'var(--red)'; });
    bUploadZone.addEventListener('drop', e => {
        e.preventDefault();
        bUploadZone.style.borderColor = 'var(--red)';
        addBulkFiles(Array.from(e.dataTransfer.files).filter(f => f.name.endsWith('.txt')));
    });

    bFileInput.addEventListener('change', e => {
        addBulkFiles(Array.from(e.target.files));
        bFileInput.value = '';
    });

    btnClearBulk.addEventListener('click', () => { bulkFiles = []; renderBulkFiles(); });

    function addBulkFiles(files) {
        files.forEach(f => {
            if (!bulkFiles.find(existing => existing.name === f.name)) {
                bulkFiles.push(f);
            }
        });
        if (files.length === 0) toast('Vui lòng chọn file .txt', 'error');
        renderBulkFiles();
    }

    function renderBulkFiles() {
        bSelect.innerHTML = '';
        bCount.textContent = `Tệp đã chọn (${bulkFiles.length})`;
        if (bulkFiles.length === 0) {
            bSelect.innerHTML = '<option>Chưa chọn tệp nào</option>';
            bBtnRun.disabled = true;
        } else {
            bulkFiles.forEach(f => {
                const opt = document.createElement('option');
                opt.textContent = f.name;
                bSelect.appendChild(opt);
            });
            bBtnRun.disabled = false;
        }
    }

    bBtnRun.addEventListener('click', () => {
        if (!bulkFiles.length) return;
        
        setLoading('#btn-run-bulk', true);
        bulkResults = [];
        $('#bulk-tbody').innerHTML = '';
        
        $('#bulk-empty').classList.add('hidden');
        $('#bulk-result-container').classList.remove('hidden');

        $('#s-live').textContent = '0';
        $('#s-dead').textContent = '0';
        $('#bulk-prog-pct').textContent = '0%';
        $('#bulk-prog-fill').style.width = '0%';
        $('#bulk-prog-lbl').textContent = 'Đang xử lý...';

        const fd = new FormData();
        bulkFiles.forEach(f => fd.append('files', f));

        fetch('/api/check-bulk', { method: 'POST', body: fd })
            .then(res => {
                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                let buf = '';

                function readStream() {
                    reader.read().then(({done, value}) => {
                        if (done) { setLoading('#btn-run-bulk', false); return; }
                        buf += decoder.decode(value, {stream: true});
                        let parts = buf.split('\n\n');
                        buf = parts.pop() || '';
                        parts.forEach(p => {
                            if (p.startsWith('data: ')) {
                                try { handleBulkSSE(JSON.parse(p.substring(6))); } catch(e){}
                            }
                        });
                        readStream();
                    });
                }
                readStream();
            })
            .catch(e => {
                toast('Lỗi tải tệp lên: ' + e.message, 'error');
                setLoading('#btn-run-bulk', false);
            });
    });

    function handleBulkSSE(d) {
        if (d.type === 'result') {
            const pct = Math.round((d.checked / d.total) * 100);
            $('#bulk-prog-pct').textContent = pct + '%';
            $('#bulk-prog-fill').style.width = pct + '%';
            $('#s-live').textContent = d.live;
            $('#s-dead').textContent = d.dead + d.error;

            bulkResults.push(d);
            
            // Add row
            // Setup variables that were accidentally deleted
            const fname = bulkFiles[d.index] ? bulkFiles[d.index].name : 'File ' + (d.index+1);
            const r = d.result;
            
            let badge = '';
            let rawStatus = 'ERROR';
            if (r.status === 'LIVE') { badge = '<span class="badge">HỢP LỆ</span>'; rawStatus = 'LIVE'; }
            else if (r.status === 'DEAD') { badge = '<span class="badge error-badge">HẾT HẠN</span>'; rawStatus = 'DEAD'; }
            else badge = '<span class="badge error-badge">LỖI</span>';

            // Generate details
            const qual = r.videoQuality && r.videoQuality !== '-' ? ` (${r.videoQuality})` : '';
            const profs = r.numProfiles + (r.profiles && r.profiles !== '-' ? ` — ${r.profiles}` : '');

            // Main row
            const tr = document.createElement('tr');
            tr.className = 'bulk-row';
            tr.dataset.status = rawStatus;
            
            tr.innerHTML = `
                <td style="cursor:pointer;">
                    <div style="display:flex; align-items:center; gap:8px;">
                        <svg class="expand-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="transition: transform 0.2s;"><polyline points="6 9 12 15 18 9"></polyline></svg>
                        ${esc(fname)}
                    </div>
                </td>
                <td style="cursor:pointer;">${badge}</td>
                <td style="cursor:pointer;">${esc(r.country || '-')}</td>
                <td style="cursor:pointer;">${esc(r.plan || '-')}</td>
                <td>
                    ${r.login_link 
                        ? `<button class="btn-outline-small copy-btn" data-link="${r.login_link}">Copy Link</button>` 
                        : '-'}
                </td>
            `;

            // Detail row
            const detailTr = document.createElement('tr');
            detailTr.className = 'bulk-detail-row';
            detailTr.style.display = 'none'; // hidden by default

            detailTr.innerHTML = `
                <td colspan="5" style="padding:0; border:none; background-color: var(--bg-dark);">
                    <div class="bulk-detail-content" style="max-height:0; overflow:hidden; transition:max-height 0.4s ease; border-bottom: 1px solid var(--border);">
                        <div style="padding: 24px 32px; display: flex; flex-direction: column; gap: 20px; max-width: 500px; margin: 0 auto;">
                            
                            <div class="info-block" style="margin-bottom:0;">
                                <div class="block-header">
                                    <h4>TỔNG QUAN TÀI KHOẢN</h4>
                                    ${badge}
                                </div>
                                <div class="info-grid">
                                    <span class="label">Gói cước:</span> <span class="value">${esc(r.plan) + qual}</span>
                                    <span class="label">Quốc gia:</span> <span class="value">${esc(r.country)}</span>
                                    <span class="label">Email:</span> <span class="value">${esc(r.email || '-')}</span>
                                    <span class="label">Gia hạn:</span> <span class="value">${esc(r.billing || '-')}</span>
                                    <span class="label">Thanh toán:</span> <span class="value">${esc(r.paymentType || '-')}</span>
                                    <span class="label">Hồ sơ:</span> <span class="value">${esc(profs || '-')}</span>
                                </div>
                            </div>
                            
                            <div class="info-block" style="margin-bottom:0;">
                                <div class="block-header">
                                    <h4>THÔNG TIN TOKEN</h4>
                                </div>
                                <div class="info-grid token-grid">
                                    <span class="label span-all">Link Đăng nhập:</span>
                                    <div class="token-box span-all">${r.login_link || '<span class="error-text">Không có link</span>'}</div>
                                    <span class="label span-all" style="margin-top:10px;">Mã Token:</span>
                                    <div class="token-box span-all" style="max-height:80px">${r.nftoken || '-'}</div>
                                </div>
                            </div>

                        </div>
                    </div>
                </td>
            `;

            // Click event to toggle
            tr.addEventListener('click', (e) => {
                if(e.target.closest('button')) return; // ignore clicks on copy button
                const isOpen = detailTr.style.display !== 'none';
                const content = detailTr.querySelector('.bulk-detail-content');
                const icon = tr.querySelector('.expand-icon');
                
                if (isOpen) {
                    content.style.maxHeight = '0px';
                    icon.style.transform = 'rotate(0deg)';
                    setTimeout(() => { if (content.style.maxHeight === '0px') detailTr.style.display = 'none'; }, 400);
                } else {
                    detailTr.style.display = 'table-row';
                    icon.style.transform = 'rotate(180deg)';
                    // Force reflow
                    detailTr.offsetHeight;
                    content.style.maxHeight = '1200px';
                }
            });

            const copyBtn = tr.querySelector('.copy-btn');
            if (copyBtn) {
                copyBtn.addEventListener('click', () => copyToClipboard(copyBtn.dataset.link));
            }

            $('#bulk-tbody').appendChild(tr);
            $('#bulk-tbody').appendChild(detailTr);

        } else if (d.type === 'complete') {
            setLoading('#btn-run-bulk', false);
            $('#bulk-prog-lbl').textContent = 'Xong';
            toast(`Đã xử lý xong ${d.total} tệp.`, 'success');

            const hasLive = bulkResults.some(item => item.result.status === 'LIVE');
            if (hasLive) {
                $('#btn-save-to-storage').style.display = 'block';
            }
        }
    }

    $('#btn-save-to-storage').addEventListener('click', async () => {
        const btn = $('#btn-save-to-storage');
        const oldText = btn.textContent;
        btn.textContent = 'ĐANG LƯU...';
        btn.disabled = true;

        let savedCount = 0;
        const savePromises = bulkResults.filter(item => item.result.status === 'LIVE').map(async (item) => {
            const r = item.result;
            const fileObj = bulkFiles[item.index];
            if (!fileObj) return;
            try {
                const text = await fileObj.text();
                await autoSaveCookie(text, r);
                savedCount++;
            } catch(e) {}
        });

        await Promise.all(savePromises);
        
        if (savedCount > 0) {
            toast(`Đã lưu ${savedCount} cookie hợp lệ vào kho`, 'success');
        } else {
            toast('Không có cookie hợp lệ nào được lưu mới', 'info');
        }
        
        btn.textContent = oldText;
        btn.disabled = false;
        btn.style.display = 'none';
    });

    // Filter Logic
    const filterBtns = [$('#f-all'), $('#f-live'), $('#f-error')];
    filterBtns.forEach((btn, idx) => {
        if(!btn) return;
        btn.addEventListener('click', () => {
            filterBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            const mode = ['ALL', 'LIVE', 'ERROR'][idx];
            $$('#bulk-tbody tr.bulk-row').forEach(tr => {
                const s = tr.dataset.status;
                let show = false;
                if (mode === 'ALL') show = true;
                else if (mode === 'LIVE' && s === 'LIVE') show = true;
                else if (mode === 'ERROR' && s !== 'LIVE') show = true;
                
                tr.style.display = show ? '' : 'none';
                
                // Hide its detail row if main row is hidden
                const detailTr = tr.nextElementSibling;
                if(detailTr && detailTr.classList.contains('bulk-detail-row')) {
                    if (!show) {
                        detailTr.style.display = 'none';
                        detailTr.querySelector('.bulk-detail-content').style.maxHeight = '0px';
                        tr.querySelector('.expand-icon').style.transform = 'rotate(0deg)';
                    }
                }
            });
        });
    });

    $('#btn-save-bulk').addEventListener('click', () => {
        if (!bulkResults.length) { toast('Không có kết quả để lưu', 'error'); return; }
        
        const activeBtn = document.querySelector('.toggle-btn.active');
        const activeFilter = activeBtn ? activeBtn.id : 'f-all';
        
        let txt = '';
        let count = 0;

        bulkResults.forEach(d => {
            const r = d.result;
            const rawStatus = (r.status === 'LIVE' && !r.error && !r.nftoken_error) ? 'LIVE' : 'ERROR';
            
            // Lọc theo tab hiện tại
            if (activeFilter === 'f-live' && rawStatus !== 'LIVE') return;
            if (activeFilter === 'f-error' && rawStatus === 'LIVE') return;

            count++;
            const fname = bulkFiles[d.index] ? bulkFiles[d.index].name : `File_${d.index+1}`;
            
            if (r.status === 'LIVE' && r.login_link) {
                txt += `${fname} | HỢP LỆ | ${r.login_link} | ${r.plan||'-'} | ${r.country||'-'} | ${r.email||'-'}\n`;
            } else if (r.status === 'DEAD') {
                txt += `${fname} | CHẾT/HỎNG\n`;
            } else {
                txt += `${fname} | LỖI: ${r.error || r.nftoken_error || '?'}\n`;
            }
        });

        if (count === 0) {
            toast('Không có bản ghi nào phù hợp với bộ lọc hiện tại', 'info');
            return;
        }

        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([txt], {type: 'text/plain'}));
        
        let filename = 'NETFLIX_ALL';
        if (activeFilter === 'f-live') filename = 'NETFLIX_LIVE';
        if (activeFilter === 'f-error') filename = 'NETFLIX_ERROR';
        
        a.download = `${filename}_${Date.now()}.txt`;
        a.click();
        toast(`Đã lưu ${count} tệp`, 'success');
    });

    // ────────────────────────────────────────────────────────────────
    // TV Login
    // ────────────────────────────────────────────────────────────────
    $('#btn-run-tv').addEventListener('click', async () => {
        const c = $('#tv-cookie').value.trim();
        const code = $('#tv-code').value.trim();
        
        if (!c || !code) { toast('Nhập Cookie và Mã TV', 'error'); return; }

        setLoading('#btn-run-tv', true);
        $('#tv-prog-lbl').textContent = 'Đang kết nối...';
        $('#tv-prog-pct').textContent = '50%';
        $('#tv-prog-fill').style.width = '50%';

        $('#tv-empty').classList.add('hidden');
        $('#tv-result-container').classList.add('hidden');
        $('#tv-error-container').classList.add('hidden');

        try {
            const res = await fetch('/api/tv-login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ cookie: c, tv_code: code })
            });
            const data = await res.json();
            
            $('#tv-prog-pct').textContent = '100%';
            $('#tv-prog-fill').style.width = '100%';
            $('#tv-prog-lbl').textContent = 'Hoàn tất';

            if (data.status === 'SUCCESS') {
                let txt = data.message;
                if (data.account) {
                    txt += `<br><br><b>Email:</b> ${esc(data.account.email)}<br><b>Quốc gia:</b> ${esc(data.account.country)}<br><b>Gói:</b> ${esc(data.account.plan)}`;
                }
                $('#tv-success-msg').innerHTML = txt;
                $('#tv-result-container').classList.remove('hidden');
            } else {
                $('#tv-error-msg').textContent = data.error || data.message || 'Lỗi không xác định';
                $('#tv-error-container').classList.remove('hidden');
            }
        } catch (e) {
            $('#tv-error-msg').textContent = 'Lỗi mạng: ' + e.message;
            $('#tv-error-container').classList.remove('hidden');
        } finally {
            setLoading('#btn-run-tv', false);
        }
    });

    // ────────────────────────────────────────────────────────────────
    // Storage Tab
    // ────────────────────────────────────────────────────────────────
    let storageData = [];

    async function loadStorage() {
        try {
            const res = await fetch('/api/storage/list');
            storageData = await res.json();
            renderStorage();
        } catch(e) {
            console.error('Failed to load storage:', e);
        }
    }

    function renderStorage() {
        const tbody = $('#storage-tbody');
        tbody.innerHTML = '';

        if (storageData.length === 0) {
            $('#storage-empty').classList.remove('hidden');
            $('#storage-list-container').classList.add('hidden');
            $('#storage-total').textContent = '0';
            $('#storage-countries').textContent = '0';
            return;
        }

        $('#storage-empty').classList.add('hidden');
        $('#storage-list-container').classList.remove('hidden');
        $('#storage-total').textContent = storageData.length;

        // Count unique countries
        const countries = new Set(storageData.map(d => d.country).filter(c => c && c !== '-'));
        $('#storage-countries').textContent = countries.size;

        storageData.forEach((item, idx) => {
            const tr = document.createElement('tr');
            tr.className = 'bulk-row storage-row'; // reuse bulk-row styling
            tr.style.animationDelay = (idx * 0.03) + 's';
            tr.style.cursor = 'pointer';

            const displayName = item.email && item.email !== '-' 
                ? esc(item.email) 
                : (item.owner && item.owner !== '-' ? esc(item.owner) : esc(item.cookie_preview || '—'));

            const savedDate = item.savedAt 
                ? new Date(item.savedAt).toLocaleDateString('vi-VN', { day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit' })
                : '-';

            const qual = item.videoQuality && item.videoQuality !== '-' ? ` (${esc(item.videoQuality)})` : '';
            const profs = item.numProfiles + (item.profiles && item.profiles !== '-' ? ` — ${esc(item.profiles)}` : '');

            // Main row
            tr.innerHTML = `
                <td>
                    <div style="display:flex; align-items:center; gap:8px;">
                        <svg class="expand-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="transition: transform 0.2s;"><polyline points="6 9 12 15 18 9"></polyline></svg>
                        <div style="font-weight:600; color: var(--text);">${displayName}</div>
                    </div>
                </td>
                <td style="font-size:11px; color:var(--text-3);">${savedDate}</td>
                <td>${esc(item.country || '-')}</td>
                <td><span style="font-weight:600;">${esc(item.plan || '-')}</span></td>
                <td>
                    <div class="storage-actions-cell" style="justify-content: flex-end;">
                        <button class="storage-action-btn btn-copy-cookie" data-id="${item.id}" title="Sao chép cookie gốc">Cookie</button>
                        ${item.login_link ? `<button class="storage-action-btn btn-copy-link" data-link="${item.login_link}" title="Sao chép link đăng nhập">Link</button>` : ''}
                        <button class="storage-action-btn btn-delete" data-id="${item.id}" title="Xoá">Xóa</button>
                    </div>
                </td>
            `;

            // Detail row
            const detailTr = document.createElement('tr');
            detailTr.className = 'bulk-detail-row';
            detailTr.style.display = 'none';

            detailTr.innerHTML = `
                <td colspan="5" style="padding:0; border:none; background-color: var(--bg-dark);">
                    <div class="bulk-detail-content" style="max-height:0; overflow:hidden; transition:max-height 0.4s ease; border-bottom: 1px solid var(--border);">
                        <div style="padding: 24px 32px; display: flex; flex-direction: column; gap: 20px; max-width: 500px; margin: 0 auto;">
                            
                            <div class="info-block" style="margin-bottom:0;">
                                <div class="block-header">
                                    <h4>TỔNG QUAN TÀI KHOẢN</h4>
                                </div>
                                <div class="info-grid">
                                    <span class="label">Gói cước:</span> <span class="value">${esc(item.plan) + qual}</span>
                                    <span class="label">Quốc gia:</span> <span class="value">${esc(item.country)}</span>
                                    <span class="label">Email:</span> <span class="value">${esc(item.email || '-')}</span>
                                    <span class="label">Gia hạn:</span> <span class="value">${esc(item.billing || '-')}</span>
                                    <span class="label">Hồ sơ:</span> <span class="value">${profs}</span>
                                </div>
                            </div>
                            
                            <div class="info-block" style="margin-bottom:0;">
                                <div class="block-header">
                                    <h4>THÔNG TIN TOKEN</h4>
                                </div>
                                <div class="info-grid token-grid">
                                    <span class="label span-all">Link Đăng nhập:</span>
                                    <div class="token-box span-all">${item.login_link || '<span class="error-text">Không có link</span>'}</div>
                                    <span class="label span-all" style="margin-top:10px;">Mã Token:</span>
                                    <div class="token-box span-all" style="max-height:80px">${item.nftoken || '-'}</div>
                                </div>
                            </div>

                        </div>
                    </div>
                </td>
            `;

            // Expand/collapse logic
            tr.addEventListener('click', (e) => {
                if(e.target.closest('button')) return; // ignore clicks on action buttons
                const isOpen = detailTr.style.display !== 'none';
                const content = detailTr.querySelector('.bulk-detail-content');
                const icon = tr.querySelector('.expand-icon');
                
                if (isOpen) {
                    content.style.maxHeight = '0px';
                    icon.style.transform = 'rotate(0deg)';
                    setTimeout(() => { if (content.style.maxHeight === '0px') detailTr.style.display = 'none'; }, 400);
                } else {
                    detailTr.style.display = 'table-row';
                    icon.style.transform = 'rotate(180deg)';
                    detailTr.offsetHeight; // force reflow
                    content.style.maxHeight = '1200px';
                }
            });

            // Action triggers
            tr.querySelector('.btn-copy-cookie').addEventListener('click', async (e) => {
                e.stopPropagation();
                try {
                    const res = await fetch('/api/storage/get-cookie', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ id: item.id })
                    });
                    const data = await res.json();
                    if (data.cookie) {
                        copyToClipboard(data.cookie);
                    } else {
                        toast('Không tìm thấy cookie', 'error');
                    }
                } catch(e) {
                    toast('Lỗi lấy cookie', 'error');
                }
            });

            const linkBtn = tr.querySelector('.btn-copy-link');
            if (linkBtn) {
                linkBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    copyToClipboard(linkBtn.dataset.link);
                });
            }

            tr.querySelector('.btn-delete').addEventListener('click', async (e) => {
                e.stopPropagation();
                if (!confirm('Xoá cookie này khỏi kho lưu trữ?')) return;
                try {
                    await fetch('/api/storage/delete', {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ id: item.id })
                    });
                    toast('Đã xoá', 'success');
                    loadStorage();
                } catch(e) {
                    toast('Lỗi xoá cookie', 'error');
                }
            });

            tbody.appendChild(tr);
            tbody.appendChild(detailTr);
        });
    }

    // Refresh button
    $('#btn-refresh-storage').addEventListener('click', () => {
        loadStorage();
        toast('Đã làm mới danh sách', 'info');
    });

    // Clear all
    $('#btn-clear-storage').addEventListener('click', async () => {
        if (!storageData.length) { toast('Kho lưu trữ trống', 'info'); return; }
        if (!confirm(`Xoá tất cả ${storageData.length} cookie đã lưu? Thao tác không thể hoàn tác.`)) return;
        try {
            await fetch('/api/storage/clear', { method: 'DELETE' });
            toast('Đã xoá tất cả', 'success');
            loadStorage();
        } catch(e) {
            toast('Lỗi xoá', 'error');
        }
    });

    // Export
    $('#btn-export-storage').addEventListener('click', () => {
        if (!storageData.length) { toast('Kho lưu trữ trống', 'info'); return; }
        window.location.href = '/api/storage/export';
        toast('Đang xuất file...', 'info');
    });

    // Load storage when switching to storage tab
    const storageTabBtn = $('#nav-storage');
    if (storageTabBtn) {
        storageTabBtn.addEventListener('click', () => {
            loadStorage();
        });
    }

})();
