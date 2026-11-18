const $ = (id) => document.getElementById(id);
let currentInfo = null;

function toast(message) {
  const el = $('toast'); el.textContent = message; el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3200);
}
function bytes(n) {
  if (!n) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}
function duration(s) {
  if (s == null) return '—';
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}` : `${m}:${String(x).padStart(2, '0')}`;
}
function safe(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function preferredVideoCodecSort() {
  const video = document.createElement('video');
  if (video.canPlayType('video/mp4; codecs="av01.0.05M.08"')) return 'vcodec:av01';
  if (video.canPlayType('video/webm; codecs="vp09.00.10.08"')) return 'vcodec:vp9.2';
  return 'vcodec:h264';
}
async function api(path, options = {}) {
  const response = await fetch(path, {headers: {'Content-Type': 'application/json', ...(options.headers || {})}, ...options});
  let data = {}; try { data = await response.json(); } catch {}
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

$('extractForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const url = $('urlInput').value.trim(); if (!url) return;
  const button = $('analyzeBtn'); button.disabled = true; button.textContent = 'جارٍ التحليل...';
  $('result').classList.add('hidden');
  try {
    currentInfo = await api('/api/extract', {method: 'POST', body: JSON.stringify({url, include_playlist: true})});
    renderInfo(currentInfo, url);
  } catch (error) {
    $('result').classList.remove('hidden'); $('result').innerHTML = `<div class="result-card"><div class="error">${safe(error.message)}</div></div>`;
  } finally { button.disabled = false; button.innerHTML = 'تحليل الرابط <span>←</span>'; }
});

function renderInfo(info, url) {
  const root = $('result'); root.classList.remove('hidden');
  const formats = (info.formats || []).filter(f => f.format_id && f.ext)
    .sort((a, b) => (b.height || 0) - (a.height || 0) || (b.fps || 0) - (a.fps || 0));
  const videoFormats = formats.filter(f => f.vcodec && f.vcodec !== 'none');
  const muxedFormats = videoFormats.filter(f => f.acodec && f.acodec !== 'none');
  const videoOnlyFormats = videoFormats.filter(f => !f.acodec || f.acodec === 'none');
  const audioFormats = formats.filter(f => f.acodec && f.acodec !== 'none' && f.vcodec === 'none');
  const requestedFormats = info.requested_formats?.length ? info.requested_formats : (info.requested_downloads || []).flatMap(item => item.requested_formats?.length ? item.requested_formats : [item]);
  const suggestedIds = requestedFormats.map(f => f.format_id).filter(Boolean);
  const suggestedLabel = suggestedIds.length ? requestedFormats.map(f => f.format || f.format_note || f.ext || f.format_id).filter(Boolean).join(' + ') : 'أفضل صيغة متاحة';
  const suggestedOption = suggestedIds.length ? `<option value="__suggested__" selected>الصيغة المقترحة · ${safe(suggestedLabel)}</option>` : '';
  const makeVideoOption = (f, kind) => {
    const label = [f.resolution || f.format_note || 'جودة غير محددة', f.ext, kind, f.fps ? `${f.fps}fps` : '', bytes(f.filesize)].filter(Boolean).join(' · ');
    return `<option value="${safe(f.format_id)}">${safe(label)}</option>`;
  };
  const videoOptions = `${suggestedOption}<option value="">تلقائي — وفق أفضل جودة متاحة</option>${muxedFormats.length ? `<optgroup label="فيديو مع صوت">${muxedFormats.map(f => makeVideoOption(f, 'فيديو وصوت')).join('')}</optgroup>` : ''}${videoOnlyFormats.length ? `<optgroup label="فيديو فقط">${videoOnlyFormats.map(f => makeVideoOption(f, 'فيديو فقط')).join('')}</optgroup>` : ''}`;
  const audioOptions = audioFormats.map(f => {
    const label = [f.format_note || f.ext, f.ext, f.acodec, bytes(f.filesize)].filter(Boolean).join(' · ');
    return `<option value="${safe(f.format_id)}">${safe(label)}</option>`;
  }).join('');

  const subtitleMap = {...(info.subtitles || {})};
  const autoMap = {...(info.automatic_captions || {})};
  const subtitleLanguages = [...new Set([...Object.keys(subtitleMap), ...Object.keys(autoMap)])].sort();
  const subtitleOptions = subtitleLanguages.length ? subtitleLanguages.map(lang =>
    `<label><input type="checkbox" class="subtitle-language" value="${safe(lang)}"> ${safe(lang)}${subtitleMap[lang] ? '' : ' (تلقائية)'}</label>`
  ).join('') : '<span class="muted">لا توجد معلومات عن الترجمات لهذا الرابط.</span>';

  const entries = info.entries || [];
  // Advanced playlist-item selection is intentionally hidden; the basic full-playlist toggle remains.

  root.innerHTML = `
    <article class="result-card">
      <div class="media-head">${info.thumbnail ? `<img class="thumb" src="${safe(info.thumbnail)}" alt="صورة المحتوى" referrerpolicy="no-referrer">` : ''}
        <div><p class="media-kicker">المحتوى الذي تم تحليله</p><h2>${safe(info.title || 'محتوى بدون عنوان')}</h2><div class="meta"><span>${safe(info.extractor || '')}</span><span>${duration(info.duration)}</span><span>${safe(info.uploader || info.channel || '')}</span>${info.playlist ? `<span>${info.playlist_count || entries.length} عنصر</span>` : ''}</div></div>
      </div>
      <section class="settings-section">
        <div class="section-heading"><h3>جودة الفيديو والصوت</h3><p>اختر الدقة والترميز ومسار الصوت وفق ما يتيحه المصدر.</p></div>
        <div class="form-grid">
          <label class="field">جودة الفيديو<select id="formatSelect">${videoOptions || '<option value="">تلقائي — وفق أفضل جودة متاحة</option>'}</select></label>
          <label class="field">الدقة المطلوبة<select id="videoResolution"><option value="">أعلى دقة متاحة</option><option value="2160">4K · 2160p</option><option value="1440">1440p</option><option value="1080">1080p</option><option value="720">720p</option><option value="480">480p</option><option value="360">360p</option></select></label>
          <label class="field">ترميز الفيديو<select id="videoCodec"><option value="quality">جودة أعلى (AV1 / VP9)</option><option value="compatibility">توافق أوسع (H.264)</option><option value="">اختيار تلقائي</option></select></label>
          <label class="field">مسار الصوت المتاح<select id="audioStreamSelect" size="1"><option value="">اختيار تلقائي</option>${audioOptions}</select><small>اختر مساراً صوتياً واحداً عند الحاجة إلى فصل الصوت عن الفيديو.</small></label>
          <label class="field">صيغة الصوت<select id="audioFormat"><option value="" selected>الاحتفاظ بالصيغة الأصلية</option><option value="mp3">MP3</option><option value="m4a">M4A</option><option value="opus">OPUS</option><option value="wav">WAV</option><option value="flac">FLAC</option><option value="aac">AAC</option><option value="alac">ALAC</option></select></label>
          <label class="field">جودة الصوت<select id="audioQuality"><option value="0">تلقائي</option><option value="1">عالية — 192 kbps</option><option value="2">متوسطة — 128 kbps</option><option value="3">اقتصادية — 64 kbps</option><option value="4">منخفضة — 32 kbps</option></select></label>
        </div>
      </section>
      <section class="settings-section">
        <div class="section-heading"><h3>خيارات المحتوى</h3><p>حدّد نوع التنزيل والخيارات المرتبطة بالترجمات وقوائم التشغيل.</p></div>
        <div class="checks core-options">
          <label><input type="checkbox" id="audioOnly"> استخراج الصوت فقط</label>
          ${info.playlist ? '<label><input type="checkbox" id="playlist" checked> تنزيل قائمة التشغيل</label>' : ''}
          <label><input type="checkbox" id="subs"> تنزيل الترجمات</label>
          <label><input type="checkbox" id="autoSubs"> تضمين الترجمات التلقائية</label>
          <label><input type="checkbox" id="embedSubs"> دمج الترجمة داخل الفيديو</label>
          ${info.duration ? '<label><input type="checkbox" id="clipEnabled"> قص جزء محدد من الفيديو</label>' : ''}
          ${(info.chapters || []).length ? '<label><input type="checkbox" id="splitChapters"> تقسيم الفيديو حسب الفصول</label>' : ''}
        </div>
        ${info.duration ? `<div id="clipFields" class="form-grid clip-fields hidden">
          <label class="field">بداية المقطع (بالثواني)<input id="clipStart" type="number" min="0" max="${Number(info.duration)}" step="0.1" value="0"></label>
          <label class="field">نهاية المقطع (بالثواني)<input id="clipEnd" type="number" min="0" max="${Number(info.duration)}" step="0.1" value="${Number(info.duration).toFixed(1)}"></label>
        </div>` : ''}
        <label class="field rename-field">اسم الملف (اختياري)<input id="newTitle" type="text" maxlength="240" placeholder="اتركه فارغاً لاستخدام العنوان الأصلي"></label>
      </section>
      ${subtitleLanguages.length ? `<section class="subtitle-picker"><div class="field-label">لغات الترجمة المتاحة</div><div class="checks">${subtitleOptions}</div></section>` : ''}
<div class="action-row"><button class="primary-btn" id="startDownload">تنزيل الآن ↓</button><button class="subtle-btn" id="copyTitle">نسخ عنوان المحتوى</button></div>
    <div id="downloadProgress" class="download-progress hidden" role="status" aria-live="polite">
      <div class="progress-heading"><span id="progressLabel">جارٍ إعداد التنزيل...</span><span id="progressPercent">0%</span></div>
      <div class="bar"><i id="progressBar"></i></div>
      <div class="progress-meta"><span id="progressBytes"></span><span id="progressSpeed"></span><span id="progressEta"></span></div>
      <div id="progressError" class="error hidden"></div><div id="downloadLinks" class="download-links"></div>
      <button type="button" id="cancelDownload" class="cancel-job">إلغاء التنزيل</button>
    </div>
    </article>`;

  $('startDownload').onclick = async () => {
    const button = $('startDownload'); button.disabled = true;
    let downloadFrame = null;
    const clientJobId = globalThis.crypto?.randomUUID?.() || 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 3 | 8)).toString(16); });
    try {
      const playlistEnabled = $('playlist')?.checked || false;
      const selectedSubs = [...root.querySelectorAll('.subtitle-language:checked')].map(x => x.value);
      const audioOnly = $('audioOnly').checked;
      const selectedVideoId = $('formatSelect').value;
      const selectedAudioIds = $('audioStreamSelect').value ? [$('audioStreamSelect').value] : [];
      const selectedVideoFormat = formats.find(format => format.format_id === selectedVideoId);
      let formatId = null;
      if (audioOnly) formatId = selectedAudioIds.length ? selectedAudioIds.join('+') : null;
      else if (selectedVideoId === '__suggested__') formatId = suggestedIds.length ? suggestedIds.join('+') : null;
      else if (selectedVideoId && selectedAudioIds.length) formatId = [selectedVideoId, ...selectedAudioIds].join('+');
      else if (selectedVideoId && selectedVideoFormat?.acodec === 'none') formatId = `${selectedVideoId}+ba/b`;
      else if (selectedVideoId) formatId = selectedVideoId;
      else if (selectedAudioIds.length) formatId = `bv+${selectedAudioIds.join('+')}/b`;

      const requestedSort = [];
      if (!audioOnly && !selectedVideoId) {
        const codecPreference = $('videoCodec').value;
        if (codecPreference === 'compatibility') requestedSort.push('proto', 'vcodec:h264', 'ext');
        else if (codecPreference === 'quality') requestedSort.push(preferredVideoCodecSort());
        const resolution = $('videoResolution').value;
        if (resolution) requestedSort.push(`res:${resolution}`);
      }

      const clipEnabled = $('clipEnabled')?.checked || false;
      const clipStart = clipEnabled ? Number($('clipStart').value) : null;
      const clipEnd = clipEnabled ? Number($('clipEnd').value) : null;
      if (clipEnabled && (!Number.isFinite(clipStart) || !Number.isFinite(clipEnd) || clipEnd <= clipStart)) {
        throw new Error('يجب أن يكون وقت نهاية المقطع أكبر من وقت بدايته.');
      }
      const splitChapters = !audioOnly && ($('splitChapters')?.checked || false);
      downloadFrame = document.createElement('iframe');
      downloadFrame.title = 'VYRO file download';
      downloadFrame.setAttribute('aria-hidden', 'true');
      downloadFrame.tabIndex = -1;
      downloadFrame.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none;bottom:0;left:0;border:0';
      document.body.appendChild(downloadFrame);
      // Navigate the frame directly from the click gesture. The server waits
      // for this job ID, then returns the completed file as an attachment.
      downloadFrame.src = `/api/jobs/${encodeURIComponent(clientJobId)}/download`;

      const response = await api('/api/download', {method: 'POST', body: JSON.stringify({
        client_job_id: clientJobId,
        url, format_id: formatId,
        audio_only: audioOnly,
        audio_format: $('audioFormat').value || null,
        audio_quality: $('audioQuality').value,
        playlist: playlistEnabled,
        subtitles: $('subs').checked || selectedSubs.length > 0,
        auto_subtitles: $('autoSubs').checked,
        auto_translated_subtitles: false,
        subtitle_languages: selectedSubs.length ? selectedSubs.join(',') : 'en.*,.*-orig',
        embed_subtitles: $('embedSubs').checked,
        keep_subtitle_files: false,
        embed_metadata: true,
        concurrent_fragments: 8,
        merge_audio_streams: selectedAudioIds.length > 1,
        format_sort: requestedSort,
        download_sections: clipEnabled ? [`${clipStart}-${clipEnd}`] : [],
        split_chapters: splitChapters,
        new_title: $('newTitle').value.trim() || null,
        embed_chapters: !clipEnabled && !audioOnly,
        no_mtime: true,
        media_title: info.title || null,
        extractor: info.extractor || null
      })});
      toast('بدأت عملية التنزيل');
      const finalStatus = await monitorJob(response.id, button);
      if (finalStatus === 'completed') setTimeout(() => downloadFrame?.remove(), 10 * 60 * 1000);
      else if (['failed', 'cancelled', 'interrupted'].includes(finalStatus)) downloadFrame?.remove();
    } catch (error) {
      downloadFrame?.remove();
      toast(error.message);
    }
    finally { button.disabled = false; }
  };
  $('copyTitle').onclick = () => navigator.clipboard?.writeText(info.title || '').then(() => toast('تم نسخ العنوان'));
  $('formatSelect').addEventListener('change', () => {
    const selectedId = $('formatSelect').value;
    const selectedFormat = formats.find(format => format.format_id === selectedId);
    // The Android FormatPage clears separate audio tracks when a muxed
    // video+audio format or the suggested format is selected.
    if (selectedId === '__suggested__' || (selectedFormat && selectedFormat.acodec && selectedFormat.acodec !== 'none')) {
      $('audioStreamSelect').value = '';
    }
  });
  const clipToggle = $('clipEnabled');
  const splitToggle = $('splitChapters');
  if (clipToggle) clipToggle.addEventListener('change', () => {
    $('clipFields').classList.toggle('hidden', !clipToggle.checked);
    if (clipToggle.checked && splitToggle) splitToggle.checked = false;
  });
  if (splitToggle) splitToggle.addEventListener('change', () => {
    if (splitToggle.checked && clipToggle) {
      clipToggle.checked = false;
      $('clipFields')?.classList.add('hidden');
    }
  });
}

async function monitorJob(jobId, startButton) {
  const box = $('downloadProgress');
  box.classList.remove('hidden');
  $('progressError').classList.add('hidden');
  $('downloadLinks').innerHTML = '';
  $('cancelDownload').disabled = false;
  let cancelled = false;
  let finalStatus = null;
  $('cancelDownload').onclick = async () => {
    $('cancelDownload').disabled = true;
    try { await api(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {method:'POST'}); }
    catch (error) { $('progressError').textContent = error.message; $('progressError').classList.remove('hidden'); }
  };
  const labels = {queued:'بانتظار بدء التنزيل',downloading:'جارٍ تنزيل المحتوى',processing:'جارٍ تجهيز الملف النهائي',cancelling:'جارٍ إيقاف التنزيل',cancelled:'أُلغي التنزيل',completed:'اكتمل تجهيز الملف',failed:'تعذّر إكمال التنزيل',interrupted:'توقف التنزيل بعد إعادة تشغيل الخادم'};
  while (!cancelled) {
    let job;
    try { job = await api(`/api/jobs/${encodeURIComponent(jobId)}`); }
    catch (error) { $('progressError').textContent = error.message; $('progressError').classList.remove('hidden'); break; }
    finalStatus = job.status;
    const percent = Math.max(0, Math.min(100, job.percent ?? 0));
    $('progressLabel').textContent = labels[job.status] || job.status;
    $('progressPercent').textContent = `${percent}%`;
    $('progressBar').style.width = `${percent}%`;
    $('progressBytes').textContent = job.total_bytes ? `${bytes(job.downloaded_bytes)} / ${bytes(job.total_bytes)}` : (job.downloaded_bytes ? bytes(job.downloaded_bytes) : '');
    $('progressSpeed').textContent = job.speed ? `${bytes(job.speed)}/ث` : '';
    $('progressEta').textContent = job.eta ? `المتبقي ${duration(job.eta)}` : '';
    if (job.error) {
      const diagnostics = Array.isArray(job.diagnostics) && job.diagnostics.length
        ? `\n\nمؤشرات التشخيص:\n${job.diagnostics.join('\n')}` : '';
      $('progressError').textContent = job.error + diagnostics;
      $('progressError').classList.remove('hidden');
    }
    if (job.status === 'completed') {
      $('cancelDownload').classList.add('hidden');
      if (job.files?.length) {
        $('downloadLinks').innerHTML = `<a class="subtle-btn download-link" href="/api/jobs/${encodeURIComponent(jobId)}/download">إذا لم يبدأ التنزيل تلقائياً، اضغط هنا</a>`;
        toast('تم تجهيز الملف وبدء التنزيل');
      } else if (job.warning) {
        $('progressError').textContent = job.warning; $('progressError').classList.remove('hidden');
      }
      break;
    }
    if (['failed','cancelled','interrupted'].includes(job.status)) {
      $('cancelDownload').classList.add('hidden');
      break;
    }
    await new Promise(resolve => setTimeout(resolve, 1100));
  }
  startButton.disabled = false;
  return finalStatus;
}

api('/api/health').then(status => {
  $('engineStatus').textContent = `yt-dlp ${status.engine} · FFmpeg ${status.ffmpeg ? 'متاح' : 'غير متاح'}`;
}).catch(() => $('engineStatus').textContent = 'محرك التنزيل غير متصل');


// VYRO PWA installation
(() => {
  const box = $('installPrompt');
  const installBtn = $('installButton');
  const laterBtn = $('dismissInstall');
  if (!box || !installBtn || !laterBtn) return;

  const standalone = window.matchMedia('(display-mode: standalone)').matches ||
    navigator.standalone === true;
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

  let dismissed = 0;
  try { dismissed = Number(localStorage.getItem('vyro-install-dismissed') || 0); } catch {}
  const canShow = !standalone && Date.now() - dismissed > 7 * 86400000;
  let installEvent = null;

  const show = () => { if (canShow) box.classList.remove('hidden'); };
  const hide = () => box.classList.add('hidden');

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/service-worker.js').catch(() => {});
    });
  }

  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    installEvent = event;
    show();
  });

  if (ios && canShow) {
    installBtn.textContent = 'طريقة التثبيت';
    setTimeout(show, 1800);
  }

  installBtn.addEventListener('click', async () => {
    if (installEvent) {
      const event = installEvent;
      installEvent = null;
      hide();
      await event.prompt();
      await event.userChoice;
    } else if (ios) {
      alert('لتثبيت VYRO على iPhone:\n1. افتح الموقع في Safari.\n2. اضغط زر المشاركة.\n3. اختر إضافة إلى الشاشة الرئيسية.\n4. اضغط إضافة.');
    }
  });

  laterBtn.addEventListener('click', () => {
    try { localStorage.setItem('vyro-install-dismissed', String(Date.now())); } catch {}
    hide();
  });

  window.addEventListener('appinstalled', hide);
})();
