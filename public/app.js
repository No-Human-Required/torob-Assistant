const $ = s => document.querySelector(s);
let selected = null, userLat = null, userLon = null, currentOffers = [], searched = false, chatMessages = [], chatSessionId = localStorage.getItem('torob_chat_session') || '', cmpIds = [], cmpTitles = {}, filterState = { brands: [], payments: [], warranty: false, fast: false };

function money(v) { return v == null ? 'قیمت ثبت نشده' : new Intl.NumberFormat('fa-IR').format(Math.round(v)) + ' تومان' }
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m])) }
async function api(url) { const r = await fetch(url); return r.json() }

function scrollToResults() {
  const el = $('#results');
  if (!el) return;
  const top = el.getBoundingClientRect().top + window.pageYOffset - 12;
  window.scrollTo({ top: Math.max(top, 0), behavior: 'smooth' });
}

async function loadMeta() {
  const d = await api('/api/meta');
  const l = $('#location');
  (d.locations || []).filter(Boolean).forEach(x => {
    const o = document.createElement('option'); o.value = x; o.textContent = x; l.appendChild(o);
  });
  renderFilterOptions('brandFilters', d.brands || [], 'brand');
  renderFilterOptions('paymentFilters', d.payments || [], 'payment');
}

function renderFilterOptions(id, values, type) {
  const el = $('#' + id);
  el.innerHTML = values.filter(Boolean).slice(0, type === 'brand' ? 24 : 12).map((v, i) => `<label class="checkitem"><input type="checkbox" data-filter="${type}" value="${esc(v)}" id="${type}-${i}"><span>${esc(v)}</span></label>`).join('');
}

function readFilters() {
  filterState.brands = [...document.querySelectorAll('input[data-filter="brand"]:checked')].map(x => x.value);
  filterState.payments = [...document.querySelectorAll('input[data-filter="payment"]:checked')].map(x => x.value);
  filterState.warranty = $('#warrantyFilter').checked;
  filterState.fast = $('#fastFilter').checked;
  const n = filterState.brands.length + filterState.payments.length + Number(filterState.warranty) + Number(filterState.fast);
  $('#filterSummary').textContent = n ? `${new Intl.NumberFormat('fa-IR').format(n)} فیلتر فعال` : 'بدون فیلتر';
}

function card(p) {
  const img = (p.image_urls || [])[0];
  const on = cmpIds.includes(p.product_id);
  return `<article class="card"><div class="thumb">${img ? `<img src="${esc(img)}" loading="lazy" alt="${esc(p.persian_title || '')}">` : '<span>◇</span>'}</div>${cmpToggle(p, on)}<span class="tag">${esc(p.category_name || 'دسته نامعلوم')}</span><h3>${esc(p.persian_title || 'بدون عنوان')}</h3><div class="cardmeta"><span>${p.brand_name ? esc(p.brand_name) : 'برند نامعلوم'}</span><span>اطلاعات ثبت‌شده</span></div><button data-pid="${esc(p.product_id)}">دیدن فروشگاه‌ها و مقایسه</button></article>`;
}

function cmpToggle(p, on) {
  const full = !on && cmpIds.length >= 3;
  return `<label class="cmptoggle${full ? ' disabled' : ''}" title="${esc(p.persian_title || '')}"><input type="checkbox" data-cmp="${esc(p.product_id)}" ${on ? 'checked' : ''} ${full ? 'disabled' : ''}><span>مقایسه</span></label>`;
}

function renderCmpTray() {
  $('#compareArea').classList.toggle('hidden', cmpIds.length === 0);
  $('#cmpCount').textContent = new Intl.NumberFormat('fa-IR').format(cmpIds.length) + ' از ۳ انتخاب شده';
  $('#cmpChips').innerHTML = cmpIds.map(id => `<span class="cmpchip">${esc(cmpTitles[id] || 'محصول')}<button data-cmpdel="${esc(id)}" aria-label="حذف">×</button></span>`).join('');
  $('#cmpRun').disabled = cmpIds.length < 2;
}

async function runCompare() {
  if (cmpIds.length < 2) return;
  const qs = new URLSearchParams({ ids: cmpIds.join(',') });
  if (userLat != null) { qs.set('lat', userLat); qs.set('lon', userLon) }
  const d = await api('/api/compare?' + qs);
  if (d.error) { $('#compareTable').innerHTML = `<div class="empty"><h3>${esc(d.error)}</h3></div>`; return }
  const its = d.items || [];
  if (its.length < 2) { $('#compareTable').innerHTML = '<div class="empty"><h3>محصولات قابل مقایسه پیدا نشد</h3></div>'; return }
  const cols = its.length;
  const cell = v => v == null || v === '' ? '<span class="unknown">نامعلوم</span>' : esc(v);
  const head = `<thead><tr><th class="cmprowhead">ویژگی</th>${its.map(i => `<th><div class="cmphead"><div class="cmpthumb">${i.image ? `<img src="${esc(i.image)}" loading="lazy" alt="">` : '◇'}</div><b>${esc(i.title || 'بدون عنوان')}</b><small>${esc(i.brand || 'برند نامعلوم')}</small></div></th>`).join('')}</tr></thead>`;
  const priceRange = i => i.min_price == null ? null : (i.min_price === i.max_price ? money(i.min_price) : money(i.min_price) + ' تا ' + money(i.max_price));
  const body = [
    ['دسته', its.map(i => cell(i.category))],
    ['مدل', its.map(i => cell(i.model))],
    ['بازهٔ قیمت ثبت‌شده', its.map(i => cell(priceRange(i)))],
    ['ارزان‌ترین فروشگاه', its.map(i => i.cheapest_shop ? cell(i.cheapest_shop.name + (i.cheapest_shop.area ? ' · ' + i.cheapest_shop.area : '')) : '<span class="unknown">نامعلوم</span>')],
    ['تعداد فروشگاه دارای آفر', its.map(i => cell(new Intl.NumberFormat('fa-IR').format(i.shop_count)))],
    ['نزدیک‌ترین شعبه', its.map(i => i.nearest_km == null ? '<span class="unknown">موقعیت ثبت نشده</span>' : cell(i.nearest_shop.name + ' · ' + i.nearest_km + ' کیلومتر'))],
    ['آفر با گارانتی ثبت‌شده', its.map(i => cell(new Intl.NumberFormat('fa-IR').format(i.warranty_offers)))],
    ['فروشگاه با ارسال فوری', its.map(i => cell(new Intl.NumberFormat('fa-IR').format(i.fast_delivery_shops)))],
    ['موجودی', its.map(() => '<span class="unknown">نامعلوم</span>')],
    ['پاسخ فروشنده', its.map(() => '<span class="unknown">نامعلوم</span>')],
  ].map(([k, vals]) => `<tr><th class="cmprowhead">${k}</th>${vals.map(v => `<td>${v}</td>`).join('')}</tr>`).join('');
  const specs = (d.spec_rows || []).map(r => `<tr><th class="cmprowhead">${esc(r.key)}</th>${r.values.map(v => `<td>${cell(v)}</td>`).join('')}</tr>`).join('');
  $('#compareTable').innerHTML = `<div class="cmpwrap"><table class="cmptable" style="--cols:${cols}">${head}<tbody>${body}${specs ? `<tr class="cmpdivider"><th class="cmprowhead" colspan="${cols + 1}">مشخصات فنی ثبت‌شده</th></tr>${specs}` : ''}</tbody></table><p class="fine">همهٔ اعداد از آفرهای ثبت‌شده در snapshot هستند؛ موجودی و پاسخ فروشنده تأیید نشده و قیمت امروز نیست.</p></div>`;
  $('#compareTable').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function search() {
  const q = $('#q').value.trim();
  const cat = $('#category') ? $('#category').value : '';
  if (!q && !cat) { $('#q').focus(); return }
  readFilters();
  const params = new URLSearchParams({ q });
  if (cat) params.set('category', cat);
  filterState.brands.forEach(x => params.append('brand', x));
  filterState.payments.forEach(x => params.append('payment', x));
  if (filterState.warranty) params.set('warranty', '1');
  if (filterState.fast) params.set('fast_delivery', '1');
  const url = '/api/search?' + params;
  const d = await api(url);
  const items = d.items || [];
  $('#cards').innerHTML = items.map(card).join('');
  const empty = $('.empty');
  empty.classList.toggle('hidden', items.length > 0);
  if (!items.length) {
    empty.innerHTML = `<div class="empty-icon">◌</div><h3>در پوشش دادهٔ فعلی نتیجه‌ای نبود</h3><p>این یعنی این کالا در snapshot فعلی ثبت نشده — نه اینکه در بازار موجود نیست. عبارت عام‌تر یا نام دسته را امتحان کن.</p>`;
  }
  $('#resultCount').textContent = items.length ? items.length + ' محصول' : 'نتیجه‌ای در پوشش فعلی پیدا نشد';
  searched = true;
  scrollToResults();
}

async function openProduct(pid) {
  const qs = new URLSearchParams();
  if (userLat != null) { qs.set('lat', userLat); qs.set('lon', userLon) }
  if ($('#budget').value) qs.set('budget', $('#budget').value);
  if ($('#location').value) qs.set('location', $('#location').value);
  const d = await api('/api/products/' + encodeURIComponent(pid) + '?' + qs);
  if (!d.product) { alert(d.error || 'محصول پیدا نشد'); return }
  selected = d.product;
  currentOffers = d.offers || [];
  const rows = currentOffers;
  $('#cards').innerHTML = `<div class="card" style="grid-column:1/-1"><div class="sectionhead"><div><span class="eyebrow">مقایسهٔ فروشگاه‌ها</span><h2>${esc(selected.persian_title)}</h2></div><button class="ghost" data-act="back">بازگشت به نتایج</button></div>${rows.length ? rows.map((o, i) => `<div class="offerrow"><div><b>${esc(o.name || 'فروشگاه ناشناس')}</b><div class="muted">${esc(o.location_label || 'محدوده نامعلوم')} · ${esc(o.address || 'آدرس نامعلوم')}</div><div class="muted">آفر این محصول · ${o.distance_km != null ? o.distance_km + ' کیلومتر · ' : ''}موجودی: <span class="badge">نامعلوم</span> · پاسخ فروشنده: <span class="badge">نامعلوم</span></div></div><div style="text-align:left"><b class="cardprice">${money(o.price_toman)}</b><div class="muted">تغییر: ${esc(o.last_price_changed_at || 'نامعلوم')}</div><button style="margin-top:5px" data-plan="${i}">افزودن به برنامه</button></div></div>`).join('') : '<div class="empty"><h3>آفر ثبت‌شده‌ای در پوشش فعلی نیست</h3><p>این نتیجه نبود کالا در بازار را ثابت نمی‌کند. جست‌وجوی محصول مشابه را امتحان کن.</p></div>'}<div style="margin-top:15px"><button class="filterbtn" data-alt="${esc(pid)}">پیشنهاد جایگزین نزدیک</button></div></div>`;
  scrollToResults();
}

async function showAlternatives(pid) {
  const d = await api('/api/alternatives?product_id=' + encodeURIComponent(pid));
  if (!d.items?.length) { alert('جایگزین ثبت‌شده‌ای در این پوشش پیدا نشد.'); return }
  $('#cards').insertAdjacentHTML('beforeend', `<div class="card" style="grid-column:1/-1"><b>گزینه‌های مشابه — تفاوت مشخصات را بررسی کن</b><div class="grid" style="margin-top:10px">${d.items.map(card).join('')}</div></div>`);
}

function makePlan(i) {
  const o = currentOffers[i];
  if (!o) return;
  selected = selected || {};
  $('#planTitle').textContent = selected.persian_title || 'محصول انتخابی';
  $('#planText').innerHTML = `<b>${esc(o.name || 'فروشگاه')}</b> · ${esc(o.location_label || 'محدوده نامعلوم')}<br>${esc(o.address || 'آدرس نامعلوم')}<br><span class="badge">آفر ثبت‌شده — موجودی تأیید نشده</span>`;
  $('#plan').classList.remove('hidden');
  $('#plan').scrollIntoView({ behavior: 'smooth' });
}

// Single delegated handler: no inline JSON in attributes (that broke on embedded quotes).
$('#cards').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.pid) openProduct(b.dataset.pid);
  else if (b.dataset.plan != null) makePlan(Number(b.dataset.plan));
  else if (b.dataset.alt) showAlternatives(b.dataset.alt);
  else if (b.dataset.act === 'back') { if (searched) search(); else window.scrollTo({ top: 0, behavior: 'smooth' }) }
});

// Compare selection: change event is required for checkboxes inside dynamically rendered cards.
$('#cards').addEventListener('change', e => {
  const cb = e.target.closest('input[data-cmp]');
  if (!cb) return;
  const id = cb.dataset.cmp;
  const title = cb.closest('.card')?.querySelector('h3')?.textContent || '';
  if (cb.checked) {
    if (cmpIds.length >= 3) { cb.checked = false; return }
    if (!cmpIds.includes(id)) { cmpIds.push(id); cmpTitles[id] = title }
  } else {
    cmpIds = cmpIds.filter(x => x !== id);
  }
  renderCmpTray();
  // Re-render only the disabled state of other checkboxes, without touching the rest.
  document.querySelectorAll('#cards input[data-cmp]').forEach(x => {
    const isOn = cmpIds.includes(x.dataset.cmp);
    x.checked = isOn;
    x.disabled = !isOn && cmpIds.length >= 3;
    x.closest('.cmptoggle')?.classList.toggle('disabled', x.disabled);
  });
});

$('#cmpChips').addEventListener('click', e => {
  const b = e.target.closest('button[data-cmpdel]');
  if (!b) return;
  const id = b.dataset.cmpdel;
  cmpIds = cmpIds.filter(x => x !== id);
  delete cmpTitles[id];
  document.querySelectorAll('#cards input[data-cmp]').forEach(x => {
    if (x.dataset.cmp === id) { x.checked = false; x.disabled = false; x.closest('.cmptoggle')?.classList.remove('disabled') }
  });
  renderCmpTray();
  if (cmpIds.length < 2) $('#compareTable').innerHTML = '';
});
$('#cmpClear').onclick = () => {
  cmpIds = []; cmpTitles = {};
  document.querySelectorAll('#cards input[data-cmp]').forEach(x => { x.checked = false; x.disabled = false; x.closest('.cmptoggle')?.classList.remove('disabled') });
  renderCmpTray(); $('#compareTable').innerHTML = '';
};
$('#cmpRun').onclick = runCompare;

document.querySelector('.filterpanel').addEventListener('change', e => {
  if (e.target.matches('input[data-filter], #warrantyFilter, #fastFilter')) readFilters();
});

$('#search').onclick = search;
$('#q').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); search() } };
$('#planBtn').onclick = () => { $('#plan').classList.remove('hidden'); $('#plan').scrollIntoView({ behavior: 'smooth' }) };
$('#closePlan').onclick = () => $('#plan').classList.add('hidden');
$('#useLocation').onclick = () => navigator.geolocation?.getCurrentPosition(x => { userLat = x.coords.latitude; userLon = x.coords.longitude; $('#useLocation').textContent = '✓ موقعیت ثبت شد' }, () => alert('موقعیت در دسترس نیست یا اجازه داده نشد.'));
$('#metaBtn').onclick = async () => {
  const d = await api('/api/health');
  $('#stats').innerHTML = '<div class="statgrid">' + Object.entries(d.counts).map(([k, v]) => `<div class="stat"><b>${new Intl.NumberFormat('fa-IR').format(v)}</b><small>${k}</small></div>`).join('') + '</div>';
  $('#modal').classList.remove('hidden');
};
$('#closeModal').onclick = () => $('#modal').classList.add('hidden');

document.querySelectorAll('.entry button').forEach(b => b.onclick = () => {
  if (b.dataset.q) { $('#q').value = b.dataset.q; search() }
  else if (b.dataset.store) { document.querySelector('.searchbox').scrollIntoView({ behavior: 'smooth' }); $('#location').focus() }
  else { document.querySelector('.searchbox').scrollIntoView({ behavior: 'smooth' }); $('#q').focus() }
});

loadMeta();

async function sendChat(){
  const input=$('#chatInput'), text=input.value.trim(); if(!text)return;
  chatMessages.push({role:'user',content:text});
  $('#chatMessages').insertAdjacentHTML('beforeend',`<div class="chatmsg user">${esc(text)}</div><div class="chatmsg assistant" id="chatTyping">در حال بررسی دیتاست ترب و فکر کردن...</div>`); input.value=''; $('#chatMessages').scrollTop=999999;
  try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:chatSessionId,messages:chatMessages})});const d=await r.json();$('#chatTyping')?.remove();if(d.session_id){chatSessionId=d.session_id;localStorage.setItem('torob_chat_session',chatSessionId)}const answer=d.answer||d.error||'پاسخی دریافت نشد.';chatMessages.push({role:'assistant',content:answer});$('#chatMessages').insertAdjacentHTML('beforeend',`<div class="chatmsg assistant">${esc(answer)}</div>`)}catch(e){$('#chatTyping')?.remove();$('#chatMessages').insertAdjacentHTML('beforeend','<div class="chatmsg assistant">ارتباط با سرور چت برقرار نشد.</div>')}
  $('#chatMessages').scrollTop=999999;
}
$('#chatSend').onclick=sendChat; $('#chatInput').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();sendChat()}};
async function loadChatHistory(){if(!chatSessionId)return;try{const r=await fetch('/api/chat/history?session_id='+encodeURIComponent(chatSessionId));const d=await r.json();if(d.messages?.length){chatMessages=d.messages;$('#chatMessages').innerHTML=d.messages.map(m=>`<div class="chatmsg ${m.role==='user'?'user':'assistant'}">${esc(m.content)}</div>`).join('')}}catch(e){}}
loadChatHistory();
