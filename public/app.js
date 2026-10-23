const $ = s => document.querySelector(s);
let selected = null, userLat = null, userLon = null, currentOffers = [], searched = false, chatMessages = [], chatSessionId = localStorage.getItem('torob_chat_session') || '', cmpIds = [], cmpTitles = {}, filterState = { brands: [], payments: [], warranty: false, fast: false };

function money(v) { return v == null ? 'قیمت ثبت نشده' : new Intl.NumberFormat('fa-IR').format(Math.round(v)) + ' تومان' }
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m])) }
async function api(url) { const r = await fetch(url); return r.json() }

async function updateRealMap(q = $('#q')?.value.trim() || '') {
  const map = $('#miniMap'); if (!map) return;
  const location = $('#location')?.value || '';
  try {
    const d = await api('/api/map-points?' + new URLSearchParams({ q, location }));
    const points = d.items || [];
    if (!points.length) {
      mapState(q ? 'برای این جست‌وجو فروشگاهِ مختصات‌دار ثبت نشده' : 'در این محدوده فروشگاهِ مختصات‌دار ثبت نشده');
      if (window.TorobMap) TorobMap.clear();
      return;
    }
    mapState('در حال نمایش نقاط ثبت‌شده…');
    const ok = window.TorobMap && TorobMap.render(points);
    const n = new Intl.NumberFormat('fa-IR').format(points.length);
    mapState(ok
      ? (q ? `${n} فروشگاه دارای آفر روی نقشه` : `${n} فروشگاه ثبت‌شده روی نقشه`)
      : 'نقشه در دسترس نیست؛ نقاط در نتایج پایین هست');
  } catch (e) {
    mapState('دریافت نقاط نقشه ناموفق بود');
    if (window.TorobMap) TorobMap.fail();
  }
}

function mapState(text) { const s = $('#mapState'); if (s) s.textContent = text }

// Leaflet + OpenStreetMap: real street tiles, real registered shop coordinates.
// Tiles need the network; when they fail the page falls back to a clear notice,
// never to invented pins. `var` (not const) so it is reachable as window.TorobMap.
var TorobMap = (() => {
  let map = null, layer = null, userMark = null, tilesOk = false, tilesTried = 0, lastPoints = [];
  const TEHRAN = [35.7000, 51.4000];

  function init() {
    const el = $('#miniMap');
    if (!el || map || !window.L) return map;
    map = L.map(el, { center: TEHRAN, zoom: 11, zoomControl: true, scrollWheelZoom: false, attributionControl: true });
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, minZoom: 9, tileSize: 256,
      attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'
    });
    tiles.on('load', () => { tilesOk = true; $('#mapFallback')?.classList.add('hidden'); });
    tiles.on('tileerror', () => {
      tilesTried++;
      if (!tilesOk && tilesTried >= 3) fail();
    });
    tiles.addTo(map);
    layer = L.layerGroup().addTo(map);
    // Keep the RTL layout honest when the sidebar is resized by media queries.
    window.addEventListener('resize', () => map && map.invalidateSize());
    return map;
  }

  function fail() {
    $('#mapFallback')?.classList.remove('hidden');
    $('#miniMap')?.classList.add('mapoffline');
  }

  function clear() {
    if (layer) layer.clearLayers();
    if (userMark) { userMark.remove(); userMark = null; }
  }

  function render(points) {
    if (!init()) return false;
    clear();
    const latlngs = [];
    points.slice(0, 120).forEach(p => {
      const lat = Number(p.lat), lon = Number(p.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
      const l = mapLinks(lat, lon, p.name);
      const route = l
        ? `<div style="display:flex;gap:6px;margin-top:7px"><a href="${l.neshanDir}" target="_blank" rel="noopener noreferrer" style="background:#1b9a71;color:#fff;border-radius:6px;padding:4px 8px;text-decoration:none;font-size:11px">مسیریابی نشان</a><a href="${l.balad}" target="_blank" rel="noopener noreferrer" style="background:#eef4ff;color:#2458ca;border-radius:6px;padding:4px 8px;text-decoration:none;font-size:11px">بلد</a></div>`
        : '';
      L.circleMarker([lat, lon], {
        radius: 7, color: '#ffffff', weight: 2, fillColor: '#e15d62', fillOpacity: .95
      }).addTo(layer).bindPopup(
        `<b>${esc(p.name || 'فروشگاه ثبت‌شده')}</b><br>${esc(p.location_label || 'محدوده نامعلوم')}<br>` +
        `<span style="color:#a36d00;font-size:11px">مختصات ثبت‌شدهٔ ترب · موجودی تأیید نشده</span>${route}`,
        { closeButton: true, minWidth: 170 }
      );
      latlngs.push([lat, lon]);
    });
    if (userLat != null && userLon != null) {
      userMark = L.circleMarker([userLat, userLon], {
        radius: 8, color: '#ffffff', weight: 3, fillColor: '#21b573', fillOpacity: 1
      }).addTo(map).bindPopup('موقعیت تقریبی شما');
      latlngs.push([userLat, userLon]);
    }
    lastPoints = latlngs;
    fit(lastPoints);
    return true;
  }

  // Fit the camera to the current points. Zoom caps depend on the container size,
  // so this is re-run after the map is expanded or the viewport changes.
  function fit(points) {
    if (!map || !points || !points.length) return;
    if (points.length === 1) { map.setView(points[0], 15); return }
    const big = map.getSize().x > 700;
    map.fitBounds(points, { padding: [big ? 48 : 22, big ? 48 : 22], maxZoom: big ? 15 : 13 });
  }

  function refit() { if (map) { map.invalidateSize(); fit(lastPoints) } }

  function setUser(lat, lon) {
    if (!init()) return;
    if (userMark) userMark.remove();
    userMark = L.circleMarker([lat, lon], {
      radius: 8, color: '#ffffff', weight: 3, fillColor: '#21b573', fillOpacity: 1
    }).addTo(map).bindPopup('موقعیت تقریبی شما');
    map.setView([lat, lon], 12);
  }

  // Leaflet must be told when its container resized (expand toggle, media query, resize).
  function invalidate() { refit() }

  return { init, render, clear, fail, setUser, invalidate, get ready() { return !!map } };
})();

function invalidate() { if (window.TorobMap) TorobMap.invalidate() }

document.addEventListener('DOMContentLoaded', () => {
  TorobMap.init();
  // Paint the registered shop points immediately, so the rail map is never an empty box.
  updateRealMap('');
  const btn = $('#mapExpand'), wrap = $('#miniMap')?.closest('.mapwrap');
  if (btn && wrap) btn.onclick = () => {
    const open = wrap.classList.toggle('expanded');
    document.body.classList.toggle('map-locked', open);
    btn.textContent = open ? '✕ بستن نقشه' : '⤢ بزرگ‌نمایی';
    btn.setAttribute('aria-label', open ? 'بستن نقشه' : 'بزرگ کردن نقشه');
    setTimeout(invalidate, 80);
  };
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && wrap?.classList.contains('expanded')) btn.click();
  });
});

// Map deep links. Iranian apps come first: Google Maps is blocked for many IR users.
function mapLinks(lat, lon, label) {
  if (lat == null || lon == null) return null;
  const dest = `${lat},${lon}`;
  const from = userLat != null ? `&origin=${userLat},${userLon}` : '';
  return {
    dest,
    neshan: `https://neshan.org/maps/@${lat},${lon},17z`,
    neshanDir: userLat != null
      ? `https://neshan.org/maps/directions/${userLat},${userLon}/${lat},${lon}`
      : `https://neshan.org/maps/@${lat},${lon},17z`,
    balad: `https://balad.ir/location?latitude=${lat}&longitude=${lon}`,
    google: `https://www.google.com/maps/dir/?api=1&destination=${dest}${from}`,
    osm: `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=18/${lat}/${lon}`,
  };
}

function mapBlock(o) {
  const m = o && o.map;
  if (!m || !m.has_coords) {
    return `<div class="mapblock mapmissing"><b>مختصات ثبت نشده</b><p>برای این فروشگاه نقطهٔ نقشه در snapshot نیست. با آدرس و نام پاساژ از نگهبانی یا کسب‌وکار نزدیک مقصد راهنمایی بگیر.</p></div>`;
  }
  const l = mapLinks(m.lat, m.lon, o.name);
  return `<div class="mapblock"><div class="mapmeta"><b>مسیر تا فروشگاه</b><span class="muted">نقطهٔ ثبت‌شدهٔ ترب — ورودی و طبقه را در محل تأیید کن</span></div><div class="maplinks"><a class="primarymap" href="${l.neshanDir}" target="_blank" rel="noopener noreferrer">مسیریابی نشان</a><a class="primarymap" href="${l.balad}" target="_blank" rel="noopener noreferrer">بلد</a><a href="${l.google}" target="_blank" rel="noopener noreferrer">گوگل مپ</a></div><button class="filterbtn lostbtn" data-lost="${esc(m.destination)}">پیدا نکردم، راهنمایی می‌خواهم</button></div>`;
}

function lostGuide(o) {
  const area = o?.location_label || o?.area || 'محدودهٔ ثبت‌شده';
  const addr = o?.address || 'آدرس ثبت نشده';
  const shop = o?.name || 'فروشگاه';
  $('#planTitle').textContent = 'راهنمای رسیدن به مقصد';
  $('#planText').innerHTML = `<b>${esc(shop)}</b> · ${esc(area)}<br>${esc(addr)}<div class="loststeps"><b>اگر گم شدی:</b><ol><li>نام پاساژ و طبقه را در ورودی از نگهبانی یا فروشندهٔ همسایه بپرس.</li><li>شماره پلاک/واحد را در راهرو تطبیق بده؛ شماره‌های پاساژها در ترب ثبت نشده‌اند.</li><li>اگر مغازه جابه‌جا شده بود، همین آفر ثبت‌شده است و موجودی تأییدشده نیست.</li><li>بدون تماس تلفنی: شمارهٔ فروشگاه‌ها در این داده جعلی است و نمایش داده نمی‌شود.</li></ol></div>`;
  $('#plan').classList.remove('hidden');
  $('#plan').scrollIntoView({ behavior: 'smooth' });
}

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
  const meta = [];
  if (p.min_price != null) meta.push(`<span class="sortbadge">از ${money(p.min_price)}</span>`);
  if (p.nearest_km != null) meta.push(`<span class="sortbadge">${p.nearest_km} کیلومتر</span>`);
  if (p.newest_price_change) meta.push(`<span class="sortbadge">آخرین تغییر: ${esc(String(p.newest_price_change).slice(0,10))}</span>`);
  return `<article class="card"><div class="thumb">${img ? `<img src="${esc(img)}" loading="lazy" alt="${esc(p.persian_title || '')}">` : '<span>◇</span>'}</div>${cmpToggle(p, on)}<span class="tag">${esc(p.category_name || 'دسته نامعلوم')}</span><h3>${esc(p.persian_title || 'بدون عنوان')}</h3>${meta.length ? `<div class="sortmeta">${meta.join('')}</div>` : ''}<div class="cardmeta"><span>${p.brand_name ? esc(p.brand_name) : 'برند نامعلوم'}</span><span class="stockbadge ${p.stock_status==='available'?'known':''}">${p.stock_status==='available' ? 'موجودی اعلام‌شده' : 'موجودی نامعلوم'}</span></div><div class="cardactions"><button data-pid="${esc(p.product_id)}">دیدن فروشگاه‌ها و مقایسه</button><button class="notifybtn" data-notify="${esc(p.product_id)}">خبرم کن</button></div></article>`;
}


// Stock is a claim with a timestamp, never "in stock". Unknown must stay visibly
// unknown: absence of a seller confirmation is not evidence of availability.
function stockChip(o) {
  if (o.stock_status === 'available') {
    const t = o.stock_confirmed_at ? ' · تأیید ' + esc(String(o.stock_confirmed_at).slice(0, 10)) : '';
    return `<span class="badge stockknown">فروشنده موجودی اعلام کرده${t}</span>`;
  }
  if (o.stock_status === 'out_of_stock') return `<span class="badge stockout">اعلام ناموجودی</span>`;
  return `<span class="badge">موجودی نامعلوم</span>`;
}
function stockLine(o) {
  return `<div class="muted">آفر این محصول · ${o.distance_km != null ? o.distance_km + ' کیلومتر · ' : ''}${stockChip(o)} · رزرو نشده؛ شرایط نهایی را پیش از خرید بررسی کن</div>`;
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
  const head = `<thead><tr><th class="cmprowhead">ویژگی</th>${its.map(i => `<th><div class="cmphead">${i.image ? `<img class="cmpheadimage" src="${esc(i.image)}" loading="lazy" alt="${esc(i.title || '')}">` : '<div class="cmpthumb">◇</div>'}<b>${esc(i.title || 'بدون عنوان')}</b><small>${esc(i.brand || 'برند نامعلوم')}</small></div></th>`).join('')}</tr></thead>`;
  const priceRange = i => i.min_price == null ? null : (i.min_price === i.max_price ? money(i.min_price) : money(i.min_price) + ' تا ' + money(i.max_price));
  const body = [
    ['دسته', its.map(i => cell(i.category))],
    ['مدل', its.map(i => cell(i.model))],
    ['بازهٔ قیمت ثبت‌شده', its.map(i => cell(priceRange(i)))],
    ['ارزان‌ترین فروشگاه', its.map(i => i.cheapest_shop ? cell(i.cheapest_shop.name + (i.cheapest_shop.area ? ' · ' + i.cheapest_shop.area : '')) : '<span class="unknown">نامعلوم</span>')],
    ['تعداد فروشگاه دارای آفر', its.map(i => cell(new Intl.NumberFormat('fa-IR').format(i.shop_count)))],
    ['نزدیک‌ترین شعبه', its.map(i => i.nearest_km == null ? '<span class="unknown">موقعیت ثبت نشده</span>' : cell(i.nearest_shop.name + ' · ' + i.nearest_km + ' کیلومتر'))],
    ['مسیر', its.map(i => {
      const s = i.cheapest_shop;
      const l = s ? mapLinks(s.lat, s.lon, s.name) : null;
      return l ? `<div class="cmpmaplinks"><a class="primarymap" href="${l.neshanDir}" target="_blank" rel="noopener noreferrer">نشان</a><a class="primarymap" href="${l.balad}" target="_blank" rel="noopener noreferrer">بلد</a><a href="${l.google}" target="_blank" rel="noopener noreferrer">گوگل</a></div>` : '<span class="unknown">نامعلوم</span>';
    })],
    ['آفر با گارانتی ثبت‌شده', its.map(i => cell(new Intl.NumberFormat('fa-IR').format(i.warranty_offers)))],
    ['فروشگاه با ارسال فوری', its.map(i => cell(new Intl.NumberFormat('fa-IR').format(i.fast_delivery_shops)))],
    ['موجودی', its.map(() => '<span class="unknown">نامعلوم</span>')],
    ['پاسخ فروشنده', its.map(() => '<span class="unknown">نامعلوم</span>')],
  ].map(([k, vals]) => `<tr><th class="cmprowhead">${k}</th>${vals.map(v => `<td>${v}</td>`).join('')}</tr>`).join('');
  const specs = (d.spec_rows || []).map(r => `<tr><th class="cmprowhead">${esc(r.key)}</th>${r.values.map(v => `<td>${cell(v)}</td>`).join('')}</tr>`).join('');
  $('#compareTable').innerHTML = `<div class="cmpwrap"><table class="cmptable" style="--cols:${cols}">${head}<tbody>${body}${specs ? `<tr class="cmpdivider"><th class="cmprowhead" colspan="${cols + 1}">مشخصات فنی ثبت‌شده</th></tr>${specs}` : ''}</tbody></table><p class="fine">همهٔ اعداد از آفرهای ثبت‌شده در snapshot هستند؛ موجودی و پاسخ فروشنده تأیید نشده و قیمت امروز نیست.</p></div>`;
  $('#compareTable').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function updateDiscovery(items) {
  const first = items && items[0];
  if (first) {
    const img = (first.image_urls || [])[0];
    $('#spotlightTitle').textContent = first.persian_title || 'محصول پیشنهادی';
    $('#spotlightText').textContent = `${first.brand_name || 'برند نامعلوم'} · اطلاعات ثبت‌شده از دیتاست ترب`;
    $('#spotlightImage').innerHTML = img ? `<img src="${esc(img)}" loading="lazy" alt="${esc(first.persian_title || '')}">` : '◇';
    $('#spotlightAction').textContent = 'دیدن فروشگاه‌ها و مقایسه ←';
    $('#spotlightAction').onclick = () => openProduct(first.product_id);
  }
}

async function search() {
  const q = $('#q').value.trim();
  const cat = window.activeCategory || ($('#category') ? $('#category').value : '');
  if (!q && !cat) { $('#q').focus(); return }
  readFilters();
  const params = new URLSearchParams({ q });
  if (cat) params.set('category', cat);
  filterState.brands.forEach(x => params.append('brand', x));
  filterState.payments.forEach(x => params.append('payment', x));
  if (filterState.warranty) params.set('warranty', '1');
  if (filterState.fast) params.set('fast_delivery', '1');
  const sort = $('#sort') ? $('#sort').value : 'relevance';
  if (sort && sort !== 'relevance') params.set('sort', sort);
  if (userLat != null) { params.set('lat', userLat); params.set('lon', userLon) }
  const url = '/api/search?' + params;
  const d = await api(url);
  if (d.error) {
    $('#cards').innerHTML = '';
    $('.empty').classList.remove('hidden');
    $('.empty').innerHTML = `<div class="empty-icon">◌</div><h3>${esc(d.error)}</h3><p>${esc(d.detail || 'دوباره تلاش کن.')}</p>`;
    $('#resultCount').textContent = 'خطا در جست‌وجو';
    return;
  }
  const items = d.items || [];
  updateDiscovery(items);
  updateRealMap(q);
  $('#cards').innerHTML = items.map(card).join('');
  const empty = $('.empty');
  empty.classList.toggle('hidden', items.length > 0);
  if (!items.length) {
    empty.innerHTML = `<div class="empty-icon">◌</div><h3>در پوشش دادهٔ فعلی نتیجه‌ای نبود</h3><p>این یعنی این کالا در snapshot فعلی ثبت نشده — نه اینکه در بازار موجود نیست. عبارت عام‌تر یا نام دسته را امتحان کن.</p>`;
  }
  // Say plainly when the requested sort could not be applied.
  const sortNote = d.note ? ` <span class="sortnote">${esc(d.note)}</span>` : '';
  $('#resultCount').innerHTML = (items.length ? new Intl.NumberFormat('fa-IR').format(items.length) + ' محصول' : 'نتیجه‌ای در پوشش فعلی پیدا نشد') + sortNote;
  searched = true;
  scrollToResults();
}

async function openProduct(pid) {
  const qs = new URLSearchParams();
  if (userLat != null) { qs.set('lat', userLat); qs.set('lon', userLon) }
  if ($('#budget').value) qs.set('budget', $('#budget').value);
  if ($('#location').value) qs.set('location', $('#location').value);
  const d = await api('/api/products/' + encodeURIComponent(pid) + '?' + qs);
  if (!d.product) { (window.toast || alert)(d.error || 'محصول پیدا نشد', 'warn'); return }
  selected = d.product;
  currentOffers = d.offers || [];
  const rows = currentOffers;
  const detailImg = (selected.image_urls || [])[0];
  const detailSpecs = (selected.specs || []).slice(0, 6).filter(s => s && s.key && s.value);
  const detailInfo = `<div class="productdetail"><div class="productdetailimage">${detailImg ? `<img src="${esc(detailImg)}" alt="${esc(selected.persian_title || '')}" loading="eager">` : '<span>تصویر ثبت نشده</span>'}</div><div class="productdetailinfo"><span class="tag">${esc(selected.category_name || 'دسته نامعلوم')}</span><h2>${esc(selected.persian_title || 'بدون عنوان')}</h2><div class="detailmeta"><span>برند: <b>${esc(selected.brand_name || 'نامعلوم')}</b></span><span>مدل: <b>${esc(selected.model || 'نامعلوم')}</b></span></div>${detailSpecs.length ? `<div class="detailspecs">${detailSpecs.map(s => `<span><b>${esc(s.key)}</b>${esc(s.value)}</span>`).join('')}</div>` : ''}<p class="fine">اطلاعات محصول از snapshot ثبت‌شدهٔ ترب است.</p></div></div>`;
  $('#cards').innerHTML = `<div class="card detailcard" style="grid-column:1/-1"><div class="sectionhead"><div><span class="eyebrow">جزئیات محصول</span></div><button class="ghost" data-act="back">بازگشت به نتایج</button></div>${detailInfo}<div class="detailoffers"><span class="eyebrow">مقایسهٔ فروشگاه‌ها</span>${rows.length ? rows.map((o, i) => `<div class="offerrow"><div><b>${esc(o.name || 'فروشگاه ناشناس')}</b><div class="muted">${esc(o.location_label || 'محدوده نامعلوم')} · ${esc(o.address || 'آدرس نامعلوم')}</div><div class="muted">آفر این محصول · ${o.distance_km != null ? o.distance_km + ' کیلومتر · ' : ''}${stockLine(o)}</div>${mapBlock(o)}</div><div style="text-align:left"><b class="cardprice">${money(o.price_toman)}</b><div class="muted">قیمت اعلام‌شده توسط فروشنده · تغییر: ${esc(o.last_price_changed_at || 'نامعلوم')}</div><div class="offeracts"><button style="margin-top:5px" data-plan="${i}">افزودن به برنامه</button><button class="notifybtn" data-notifyshop="${esc(o.shop_id || o.s_id)}" data-notifypid="${esc(pid)}">خبرم کن</button><button class="ghost reportbtn" data-report="${esc(pid)}" data-reportshop="${esc(o.shop_id || o.s_id)}">گزارش مغایرت</button></div></div></div>`).join('') : '<div class="empty"><h3>آفر ثبت‌شده‌ای در پوشش فعلی نیست</h3><p>این نتیجه نبود کالا در بازار را ثابت نمی‌کند. جست‌وجوی محصول مشابه را امتحان کن.</p></div>'}<div style="margin-top:15px"><button class="filterbtn" data-alt="${esc(pid)}">پیشنهاد جایگزین نزدیک</button></div></div></div>`;
  scrollToResults();
}

async function showAlternatives(pid) {
  const d = await api('/api/alternatives?product_id=' + encodeURIComponent(pid));
  if (!d.items?.length) { (window.toast || alert)('جایگزین ثبت‌شده‌ای در این پوشش پیدا نشد.', 'warn'); return }
  $('#cards').insertAdjacentHTML('beforeend', `<div class="card" style="grid-column:1/-1"><b>گزینه‌های مشابه — تفاوت مشخصات را بررسی کن</b><div class="grid" style="margin-top:10px">${d.items.map(card).join('')}</div></div>`);
}

function makePlan(i) {
  const o = currentOffers[i];
  if (!o) return;
  selected = selected || {};
  const l = mapLinks(o.map?.lat, o.map?.lon, o.name);
  $('#planTitle').textContent = selected.persian_title || 'محصول انتخابی';
  $('#planText').innerHTML = `<b>${esc(o.name || 'فروشگاه')}</b> · ${esc(o.location_label || 'محدوده نامعلوم')}<br>${esc(o.address || 'آدرس نامعلوم')}<br><span class="badge">آفر ثبت‌شده — موجودی تأیید نشده</span>${l ? `<div class="maplinks planmaplinks"><a class="primarymap" href="${l.neshanDir}" target="_blank" rel="noopener noreferrer">مسیریابی نشان</a><a class="primarymap" href="${l.balad}" target="_blank" rel="noopener noreferrer">بلد</a><a href="${l.google}" target="_blank" rel="noopener noreferrer">گوگل مپ</a></div>` : ''}<button class="filterbtn lostbtn" data-lostshop="${i}">پیدا نکردم، راهنمایی می‌خواهم</button>`;
  $('#plan').classList.remove('hidden');
  $('#plan').scrollIntoView({ behavior: 'smooth' });
}

// Single delegated handler: no inline JSON in attributes (that broke on embedded quotes).
$('#cards').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.pid) openProduct(b.dataset.pid);
  else if (b.dataset.notify) window.TorobShell?.openNotify(b.dataset.notify);
  else if (b.dataset.notifyshop) window.TorobShell?.openNotify(b.dataset.notifypid, b.dataset.notifyshop);
  else if (b.dataset.report) window.TorobShell?.openReport(b.dataset.report, b.dataset.reportshop);
  else if (b.dataset.plan != null) makePlan(Number(b.dataset.plan));
  else if (b.dataset.alt) showAlternatives(b.dataset.alt);
  else if (b.dataset.lost) { const idx = currentOffers.findIndex(o => o.map && o.map.destination === b.dataset.lost); lostGuide(idx >= 0 ? currentOffers[idx] : null) }
  else if (b.dataset.act === 'back') { if (searched) search(); else window.scrollTo({ top: 0, behavior: 'smooth' }) }
});

// "I can't find it" inside the visit plan: keep the user moving instead of dead-ending.
$('#planText').addEventListener('click', e => {
  const b = e.target.closest('button[data-lostshop]');
  if (!b) return;
  const o = currentOffers[Number(b.dataset.lostshop)];
  if (o) lostGuide(o);
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
$('#q').onkeydown = e => {
  if (e.key !== 'Enter') return;
  // The typeahead owns Enter while a suggestion is highlighted.
  if (window.TorobShell?.suggestionActive?.()) { e.preventDefault(); return }
  e.preventDefault(); search();
};
// Re-run the search when the sort changes, so the list always matches the control.
if ($('#sort')) $('#sort').onchange = () => { if (searched || $('#q').value.trim()) search() };
$('#planBtn').onclick = () => { $('#plan').classList.remove('hidden'); $('#plan').scrollIntoView({ behavior: 'smooth' }) };
$('#closePlan').onclick = () => $('#plan').classList.add('hidden');
$('#useLocation').onclick = () => navigator.geolocation?.getCurrentPosition(x => {
  userLat = x.coords.latitude; userLon = x.coords.longitude;
  $('#useLocation').textContent = '✓ موقعیت ثبت شد';
  if (window.TorobMap) TorobMap.setUser(userLat, userLon);
  if (searched || $('#q').value.trim()) search();
}, () => (window.toast || alert)('موقعیت در دسترس نیست یا اجازه داده نشد.', 'warn'));
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
$('#spotlightAction').onclick = () => { $('#q').focus(); document.querySelector('.searchbox').scrollIntoView({ behavior: 'smooth' }) };
// #quickCategory is owned by shell.js (it applies a real category column, not free text).
$('#quickBudget').onchange = () => { $('#budget').value=$('#quickBudget').value; if($('#q').value.trim() || window.activeCategory) search() };

loadMeta();

// Lightweight React-Bits-style motion layer for the existing vanilla UI:
// mouse-following glow + tilt on desktop, with reduced-motion/accessibility guards.
const motionOK = !window.matchMedia('(prefers-reduced-motion: reduce)').matches && window.matchMedia('(pointer: fine)').matches;
if (motionOK) {
  let raf = 0, mx = 50, my = 25;
  document.addEventListener('pointermove', e => {
    mx = (e.clientX / Math.max(innerWidth, 1)) * 100;
    my = (e.clientY / Math.max(innerHeight, 1)) * 100;
    if (!raf) raf = requestAnimationFrame(() => {
      document.documentElement.style.setProperty('--mouse-x', `${mx}%`);
      document.documentElement.style.setProperty('--mouse-y', `${my}%`);
      raf = 0;
    });
  }, { passive: true });
  document.addEventListener('pointerover', e => {
    const card = e.target.closest('.card, .searchbox, .comparetray, .chatbox');
    if (card) card.classList.add('motion-hover');
  });
  document.addEventListener('pointerout', e => {
    const card = e.target.closest('.card, .searchbox, .comparetray, .chatbox');
    if (card && !card.contains(e.relatedTarget)) card.classList.remove('motion-hover');
  });
}

function chatProductCards(products){
  const list=(products||[]).filter(p=>p&&p.product_id);
  if(!list.length)return '';
  return `<div class="chatproducts">${list.map(p=>`<button class="chatproduct" data-chatpid="${esc(p.product_id)}"><span class="chatproductimg">${p.image?`<img src="${esc(p.image)}" loading="lazy" alt="${esc(p.title||'')}">`:'◇'}</span><span class="chatproductbody"><b>${esc(p.title||'بدون عنوان')}</b><small>${esc(p.brand||'برند نامعلوم')}${p.shop_count!=null?` · ${new Intl.NumberFormat('fa-IR').format(p.shop_count)} فروشگاه`:''}</small><span class="chatproductprice">${p.min_price==null?'قیمت ثبت نشده':money(p.min_price)+(p.max_price!=null&&p.max_price!==p.min_price?' به بالا':'')}</span></span></button>`).join('')}</div>`;
}

function chatBubble(msg){
  return `<div class="chatmsg ${msg.role==='user'?'user':'assistant'}">${esc(msg.content)}${msg.role==='assistant'?chatProductCards(msg.products):''}</div>`;
}

async function sendChat(){
  const input=$('#chatInput'), text=input.value.trim(); if(!text)return;
  chatMessages.push({role:'user',content:text});
  $('#chatMessages').insertAdjacentHTML('beforeend',`<div class="chatmsg user">${esc(text)}</div><div class="chatmsg assistant chatTyping" id="chatTyping">در حال بررسی دیتاست ترب<span class="typingdots" aria-hidden="true"><i></i><i></i><i></i></span></div>`); input.value=''; $('#chatMessages').scrollTop=999999;
  try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:chatSessionId,messages:chatMessages})});const d=await r.json();$('#chatTyping')?.remove();if(d.session_id){chatSessionId=d.session_id;localStorage.setItem('torob_chat_session',chatSessionId)}const answer=d.answer||d.error||'پاسخی دریافت نشد.';const msg={role:'assistant',content:answer};if(d.products?.length)msg.products=d.products;chatMessages.push(msg);$('#chatMessages').insertAdjacentHTML('beforeend',chatBubble(msg))}catch(e){$('#chatTyping')?.remove();$('#chatMessages').insertAdjacentHTML('beforeend','<div class="chatmsg assistant">ارتباط با سرور چت برقرار نشد.</div>')}
  $('#chatMessages').scrollTop=999999;
}
$('#chatSend').onclick=sendChat; $('#chatInput').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();sendChat()}};
async function loadChatHistory(){if(!chatSessionId)return;try{const r=await fetch('/api/chat/history?session_id='+encodeURIComponent(chatSessionId));const d=await r.json();if(d.messages?.length){chatMessages=d.messages;$('#chatMessages').innerHTML=d.messages.map(chatBubble).join('')}}catch(e){}}
loadChatHistory();

// Clicking a product card inside a chat reply opens its detail/compare view.
$('#chatMessages').addEventListener('click', e => {
  const b = e.target.closest('button[data-chatpid]');
  if (!b) return;
  openProduct(b.dataset.chatpid);
});
