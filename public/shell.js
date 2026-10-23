/* ============================================================================
   shell.js — chrome, navigation, discovery and feedback layer.
   Loaded after app.js so it can reuse its helpers ($ , esc, api, search)
   without touching the existing API/DOM contracts.
   ========================================================================== */
(() => {
  'use strict';

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const finePointer = window.matchMedia('(pointer: fine)').matches;
  const motionOK = !reduceMotion && finePointer;
  const fa = n => new Intl.NumberFormat('fa-IR').format(n);

  /* ------------------------------------------------------------------ toasts */
  const toastHost = document.getElementById('toasts');
  const ICONS = { ok: '✓', warn: '!', info: 'i' };
  function toast(message, kind = 'info', ms = 3600) {
    if (!toastHost || !message) return;
    // De-dupe the exact same message shown in a burst (e.g. repeated clicks).
    const last = toastHost.lastElementChild;
    if (last && last.dataset.msg === message && !last.classList.contains('out')) return;
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.dataset.msg = message;
    el.innerHTML = `<span class="ticon" aria-hidden="true">${ICONS[kind] || ICONS.info}</span><span></span>`;
    el.lastElementChild.textContent = message;
    toastHost.appendChild(el);
    setTimeout(() => {
      el.classList.add('out');
      setTimeout(() => el.remove(), 240);
    }, ms);
  }
  window.toast = toast;

  /* ------------------------------------------------- header / progress / spy */
  const header = document.getElementById('siteHeader');
  const progress = document.getElementById('scrollProgress');
  const toTop = document.getElementById('toTop');

  let rafPending = false;
  function onScroll() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => {
      rafPending = false;
      const y = window.scrollY;
      header?.classList.toggle('is-stuck', y > 6);
      toTop?.classList.toggle('show', y > 620);
      if (progress) {
        const max = document.documentElement.scrollHeight - window.innerHeight;
        progress.style.transform = `scaleX(${max > 0 ? Math.min(y / max, 1) : 0})`;
      }
      spy();
    });
  }
  window.addEventListener('scroll', onScroll, { passive: true });

  // Scroll-spy: highlight the nav item whose section currently owns the viewport.
  // Hidden sections (e.g. the collapsed visit plan) are skipped entirely — a
  // display:none element reports top:0 and would otherwise always "win".
  const navLinks = [...document.querySelectorAll('.navlink')];
  const spyTargets = navLinks
    .map(a => ({ link: a, el: document.querySelector(a.getAttribute('href')) }))
    .filter(x => x.el);

  function spy() {
    const visible = spyTargets.filter(t => t.el.offsetParent !== null && t.el.getBoundingClientRect().height > 0);
    if (!visible.length) { navLinks.forEach(l => l.classList.toggle('is-active', l === navLinks[0])); return }
    const line = window.scrollY + (header?.offsetHeight || 100) + 90;
    let current = visible[0];
    for (const t of visible) {
      if (t.el.getBoundingClientRect().top + window.scrollY <= line) current = t;
    }
    navLinks.forEach(l => l.classList.toggle('is-active', l === current.link));
  }

  toTop?.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' });
  });

  /* -------------------------------------------------------------- mobile nav */
  const navToggle = document.getElementById('navToggle');
  const drawer = document.getElementById('navDrawer');
  const navClose = document.getElementById('navClose');
  let lastFocus = null;

  function openDrawer() {
    lastFocus = document.activeElement;
    drawer?.classList.add('open');
    drawer?.setAttribute('aria-hidden', 'false');
    navToggle?.setAttribute('aria-expanded', 'true');
    document.body.style.overflow = 'hidden';
    setTimeout(() => navClose?.focus(), 60);
  }
  function closeDrawer() {
    drawer?.classList.remove('open');
    drawer?.setAttribute('aria-hidden', 'true');
    navToggle?.setAttribute('aria-expanded', 'false');
    document.body.style.overflow = '';
    lastFocus?.focus?.();
  }
  navToggle?.addEventListener('click', openDrawer);
  navClose?.addEventListener('click', closeDrawer);
  drawer?.addEventListener('click', e => { if (e.target === drawer) closeDrawer(); });
  drawer?.querySelectorAll('a').forEach(a => a.addEventListener('click', closeDrawer));
  document.addEventListener('keydown', e => {
    if (!drawer?.classList.contains('open')) return;
    if (e.key === 'Escape') { closeDrawer(); return }
    if (e.key !== 'Tab') return;
    // keep Tab inside the open drawer — it is aria-modal
    const focusables = [...drawer.querySelectorAll('a[href], button:not([disabled])')]
      .filter(el => el.offsetParent !== null);
    if (!focusables.length) return;
    const first = focusables[0], last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
  });
  // the drawer is a narrow-viewport affordance only
  window.addEventListener('resize', () => {
    if (window.innerWidth > 960 && drawer?.classList.contains('open')) closeDrawer();
  });
  document.getElementById('drawerMetaBtn')?.addEventListener('click', closeDrawer);

  /* -------------------------------------------------------- quick search (/ or) */
  function focusSearch() {
    const box = document.getElementById('searchBox');
    box?.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'center' });
    setTimeout(() => document.getElementById('q')?.focus(), reduceMotion ? 0 : 320);
  }
  document.getElementById('navSearchBtn')?.addEventListener('click', focusSearch);
  document.addEventListener('keydown', e => {
    const tag = (e.target.tagName || '').toLowerCase();
    const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable;
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === '/' ) { e.preventDefault(); focusSearch(); }
  });

  /* --------------------------------------------------------------- categories */
  let categories = [];
  const chipHost = document.getElementById('topCatChips');

  function paintCategories() {
    if (!chipHost) return;
    if (!categories.length) {
      chipHost.innerHTML = '<span class="catlink" style="color:#93a0af">دسته‌ای برای نمایش نیست</span>';
      return;
    }
    chipHost.innerHTML = categories
      .map(c => `<button class="catlink" type="button" data-cat="${esc(c.name)}">
          ${esc(c.name)}
          <span style="opacity:.55;font-size:10px">${fa(c.with_offers)}</span>
        </button>`)
      .join('');
    const quick = document.getElementById('quickCategory');
    if (quick) {
      quick.innerHTML = '<option value="">همه دسته‌ها</option>' +
        categories.map(c => `<option value="${esc(c.name)}">${esc(c.name)}</option>`).join('');
    }
  }

  async function loadCategories() {
    try {
      const r = await fetch('/api/top-categories?limit=10');
      const d = await r.json();
      categories = (d.items || []).filter(c => c && c.name);
    } catch (e) {
      categories = [];
    }
    paintCategories();
    requestAnimationFrame(syncStrip);
  }

  // A category is a real column, not a free-text guess: keep it out of `q`
  // so the result header and the search box never disagree.
  function applyCategory(name, { scroll = true } = {}) {
    window.activeCategory = name || '';
    document.querySelectorAll('.catlink[data-cat]').forEach(b => {
      b.classList.toggle('is-active', b.dataset.cat === name);
    });
    const quick = document.getElementById('quickCategory');
    if (quick && quick.value !== (name || '')) quick.value = name || '';
    const q = document.getElementById('q');
    if (q) q.value = '';
    if (!name) { resetResults(); if (scroll) scrollToSearch(); return }
    if (typeof window.search === 'function') window.search();
    if (scroll) scrollToSearch();
  }

  function scrollToSearch() {
    document.getElementById('searchBox')?.scrollIntoView({
      behavior: reduceMotion ? 'auto' : 'smooth', block: 'start'
    });
  }

  // Clearing every constraint returns the page to its honest "nothing searched yet" state
  // instead of leaving stale cards that no longer match any visible control.
  function resetResults() {
    const cards = document.getElementById('cards');
    if (cards) cards.innerHTML = '';
    const count = document.getElementById('resultCount');
    if (count) count.textContent = '';
    const empty = document.querySelector('#results .empty');
    if (empty) {
      empty.classList.remove('hidden');
      empty.innerHTML = '<div class="empty-icon">◌</div><h3>هنوز جست‌وجویی انجام نشده</h3>' +
        '<p>نام محصول یا کاری که می‌خواهی انجام بدهی را وارد کن.</p>';
    }
    const cmp = document.getElementById('compareArea');
    if (cmp) cmp.classList.add('hidden');
  }
  window.applyCategory = applyCategory;

  chipHost?.addEventListener('click', e => {
    const b = e.target.closest('.catlink[data-cat]');
    if (!b) return;
    const name = b.dataset.cat;
    // Clicking the active chip clears the filter, which is what people expect.
    applyCategory(window.activeCategory === name ? '' : name);
  });

  /* strip scroll affordance: arrows + edge fades appear only when needed */
  const catStrip = document.getElementById('topCatStrip');
  const catNext = document.getElementById('catNext');
  const catPrev = document.getElementById('catPrev');

  function syncStrip() {
    if (!chipHost || !catStrip) return;
    // RTL: scrollLeft runs from 0 (start/right) toward negative values.
    const maxScroll = chipHost.scrollWidth - chipHost.clientWidth;
    const pos = Math.abs(chipHost.scrollLeft);
    const hasMore = maxScroll > 4;
    catStrip.classList.toggle('can-next', hasMore && pos < maxScroll - 4);
    catStrip.classList.toggle('can-prev', hasMore && pos > 4);
    if (catNext) catNext.hidden = !(hasMore && pos < maxScroll - 4);
    if (catPrev) catPrev.hidden = !(hasMore && pos > 4);
  }

  chipHost?.addEventListener('scroll', syncStrip, { passive: true });
  window.addEventListener('resize', syncStrip);
  function nudgeStrip(dir) {
    if (!chipHost) return;
    // dir=1 reveals later items (leftwards in RTL), dir=-1 goes back.
    chipHost.scrollBy({ left: dir * Math.max(chipHost.clientWidth * 0.7, 200), behavior: 'smooth' });
  }
  catNext?.addEventListener('click', () => nudgeStrip(1));
  catPrev?.addEventListener('click', () => nudgeStrip(-1));

  document.getElementById('quickCategory')?.addEventListener('change', e => {
    applyCategory(e.target.value, { scroll: false });
  });

  // Free-text search clears the category so both controls stay truthful.
  const qInput = document.getElementById('q');
  qInput?.addEventListener('input', () => {
    if (window.activeCategory) {
      window.activeCategory = '';
      document.querySelectorAll('.catlink[data-cat]').forEach(b => b.classList.remove('is-active'));
      const quick = document.getElementById('quickCategory');
      if (quick) quick.value = '';
    }
  });

  const notifySubscriber = localStorage.getItem('torob_notify_key') || (() => { const x=crypto.randomUUID ? crypto.randomUUID() : `anon-${Date.now()}-${Math.random()}`; localStorage.setItem('torob_notify_key',x); return x })();
  const notifyInboxBtn = document.getElementById('notifyInboxBtn');
  const notifyCount = document.getElementById('notifyCount');
  async function refreshNotifications() {
    if (!notifySubscriber || !notifyCount) return;
    try { const d=await fetch('/api/notifications?subscriber_key='+encodeURIComponent(notifySubscriber)).then(r=>r.json()); const n=(d.items||[]).filter(x=>!x.read_at).length; notifyCount.textContent=fa(n); notifyCount.classList.toggle('hidden',!n); } catch (_) {}
  }
  notifyInboxBtn?.addEventListener('click', async () => {
    try { const d=await fetch('/api/notifications?subscriber_key='+encodeURIComponent(notifySubscriber)).then(r=>r.json()); const items=d.items||[]; if(!items.length){toast('هنوز اعلانی برای این کاربر ثبت نشده است.','info');return} items.slice(0,3).forEach(x=>toast(x.body,x.read_at?'info':'ok',6500)); if(items[0]) await fetch('/api/notify/read',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subscriber_key:notifySubscriber,notification_id:items[0].notification_id})}); refreshNotifications(); } catch (_){ toast('صندوق اعلان در دسترس نیست.','warn') }
  });
  refreshNotifications();

  const notifyBox = document.getElementById('inventoryNotify');
  const notifyForm = document.getElementById('notifyForm');
  const notifyChannel = document.getElementById('notifyChannel');
  const notifyPhoneWrap = document.getElementById('notifyPhoneWrap');
  function openNotify(pid, shop='') { document.getElementById('notifyProduct').value=pid; document.getElementById('notifyShop').value=shop; notifyBox?.classList.remove('hidden'); document.getElementById('notifyConsent')?.focus(); }
  function closeNotify() { notifyBox?.classList.add('hidden') }
  document.getElementById('closeNotify')?.addEventListener('click', closeNotify);
  notifyBox?.addEventListener('click',e=>{if(e.target===notifyBox)closeNotify()});

  // Report flow: subject -> optional note -> preview -> confirm -> receipt (PRD §8).
  const reportBox = document.getElementById('reportBox');
  const reportForm = document.getElementById('reportForm');
  const reportPreview = document.getElementById('reportPreview');
  function renderReportPreview() {
    const issue = document.getElementById('reportIssue');
    const label = issue?.options[issue.selectedIndex]?.textContent || '';
    if (reportPreview) reportPreview.textContent = `پیش‌نمایش: «${label}» برای همین آفر ثبت می‌شود؛ گزارش خصوصی است و موجودی را تغییر نمی‌دهد.`;
  }
  function openReport(pid, shop='') {
    document.getElementById('reportProduct').value = pid;
    document.getElementById('reportShop').value = shop;
    document.getElementById('reportNote').value = '';
    renderReportPreview();
    reportBox?.classList.remove('hidden');
    document.getElementById('reportIssue')?.focus();
  }
  function closeReport() { reportBox?.classList.add('hidden') }
  document.getElementById('closeReport')?.addEventListener('click', closeReport);
  reportBox?.addEventListener('click', e => { if (e.target === reportBox) closeReport() });
  document.getElementById('reportIssue')?.addEventListener('change', renderReportPreview);
  reportForm?.addEventListener('submit', async e => {
    e.preventDefault();
    const body = { product_id: document.getElementById('reportProduct').value, shop_id: document.getElementById('reportShop').value || null, issue_type: document.getElementById('reportIssue').value, note: document.getElementById('reportNote').value, request_id: (crypto.randomUUID ? crypto.randomUUID() : String(Date.now())) };
    try { const r = await fetch('/api/discrepancies', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) }); const d = await r.json(); if (!r.ok) throw Error(d.error || 'ثبت نشد'); toast('گزارش خصوصی ثبت شد؛ بررسی می‌شود و رتبه را خودکار تغییر نمی‌دهد.', 'ok'); closeReport(); } catch (err) { toast(err.message, 'warn') }
  });

  // Subscription management: edit/pause/cancel must survive independently of chat.
  const subsBox = document.getElementById('subsBox');
  async function openSubs() {
    subsBox?.classList.remove('hidden');
    const host = document.getElementById('subsList');
    if (host) host.innerHTML = '<p class="fine">در حال خواندن اشتراک‌ها…</p>';
    try { const d = await fetch('/api/notify/subscriptions?subscriber_key=' + encodeURIComponent(notifySubscriber)).then(r => r.json()); const items = d.items || []; if (host) host.innerHTML = items.length ? items.map(s => `<div class="subrow"><div><b>${esc(s.product_id)}</b><small>${esc(s.channel === 'sms' ? 'پیامک' : 'اعلان سایت')} · وضعیت: ${esc({active:'فعال',paused:'مکث',cancelled:'لغوشده',expired:'منقضی'}[s.status] || s.status)}${s.max_price ? ' · سقف ' + fa(s.max_price) + ' تومان' : ''}</small></div><div class="subacts"><button class="ghost" data-subact="pause" data-subid="${s.subscription_id}">مکث</button><button class="ghost" data-subact="resume" data-subid="${s.subscription_id}">ادامه</button><button class="ghost" data-subact="cancel" data-subid="${s.subscription_id}">لغو</button></div></div>`).join('') : '<p class="fine">هنوز هشداری ثبت نشده است.</p>'; } catch (_) { if (host) host.innerHTML = '<p class="fine">خواندن اشتراک‌ها ناموفق بود.</p>'; }
  }
  document.getElementById('closeSubs')?.addEventListener('click', () => subsBox?.classList.add('hidden'));
  subsBox?.addEventListener('click', async e => {
    if (e.target === subsBox) return subsBox.classList.add('hidden');
    const b = e.target.closest('[data-subact]'); if (!b) return;
    try { const r = await fetch('/api/notify/action', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ subscription_id: Number(b.dataset.subid), action: b.dataset.subact, subscriber_key: notifySubscriber }) }); const d = await r.json(); toast(d.ok ? 'وضعیت اشتراک به‌روز شد.' : 'این اشتراک پیدا نشد.', d.ok ? 'ok' : 'warn'); openSubs(); } catch (_) { toast('به‌روزرسانی اشتراک ناموفق بود.', 'warn') }
  });
  document.getElementById('navSubsBtn')?.addEventListener('click', openSubs);
  notifyChannel?.addEventListener('change',()=>notifyPhoneWrap?.classList.toggle('hidden',notifyChannel.value!=='sms'));
  notifyForm?.addEventListener('submit',async e=>{ e.preventDefault(); const body={subscriber_key:notifySubscriber,product_id:document.getElementById('notifyProduct').value,shop_id:document.getElementById('notifyShop').value||null,channel:notifyChannel.value,destination:document.getElementById('notifyPhone').value,max_price:document.getElementById('notifyMaxPrice').value||null,consent:document.getElementById('notifyConsent').checked}; try { const r=await fetch('/api/notify/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); const d=await r.json(); if(!r.ok) throw Error(d.error||'ثبت نشد'); toast(d.channel==='sms'?'هشدار ثبت شد؛ اتصال پیامک هنوز فعال نشده است.':'هشدار داخل سایت ثبت شد.','ok'); closeNotify(); } catch(err){ toast(err.message,'warn') } });

  /* ------------------------------------------------------------- seller inbox */
  const sellerHost=document.getElementById('sellerInbox');
  function sellerRow(r, isReport=false){
    if(isReport) return `<div class="sellerrow"><div><b>گزارش مغایرت #${fa(r.report_id)}</b><small>${esc(r.issue_type)} · وضعیت: ${esc(r.status)}</small><p>${esc(r.note||'بدون توضیح')}</p></div><div class="subacts"><button class="ghost" data-review="${r.report_id}" data-status="in_review">در حال بررسی</button><button class="ghost" data-review="${r.report_id}" data-status="needs_info">اطلاعات بیشتر</button></div></div>`;
    return `<div class="sellerrow"><div><b>${esc(r.alert_type==='sold_out'?'کالا تمام‌شده':'هشدار کمبود')} · ${esc(r.persian_title||r.product_id)}</b><small>باقی‌مانده: ${r.stock_qty==null?'نامعلوم':fa(r.stock_qty)} · مبنا: ${r.baseline_qty==null?'نامعلوم':fa(r.baseline_qty)} · ${esc(r.stock_source||'unknown')}</small></div><div class="subacts"><button class="ghost" data-seller-action="confirm" data-pid="${esc(r.product_id)}" data-shop="${esc(r.shop_id)}">تأیید</button><button class="ghost" data-seller-action="supply" data-pid="${esc(r.product_id)}" data-shop="${esc(r.shop_id)}">ثبت تأمین</button></div></div>`;
  }
  async function loadSeller(){ const shop=document.getElementById('sellerShop')?.value.trim()||''; sellerHost.innerHTML='<p class="fine">در حال بارگذاری…</p>'; try{const d=await fetch('/api/seller/inbox?shop_id='+encodeURIComponent(shop)).then(r=>r.json()); sellerHost.innerHTML=`<div class="sellergroup"><b>هشدارهای موجودی</b>${(d.alerts||[]).map(x=>sellerRow(x)).join('')||'<p class="fine">مورد بازی ندارید.</p>'}</div><div class="sellergroup"><b>گزارش‌های مغایرت</b>${(d.reports||[]).map(x=>sellerRow(x,true)).join('')||'<p class="fine">گزارشی برای رسیدگی نیست.</p>'}</div><p class="fine">${esc(d.note||'')}</p>`}catch(e){sellerHost.innerHTML='<p class="fine">کارتابل در دسترس نیست.</p>'} }
  document.getElementById('sellerLoad')?.addEventListener('click',loadSeller);
  sellerHost?.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;const shop=document.getElementById('sellerShop').value.trim();try{let body;if(b.dataset.review) body={report_id:Number(b.dataset.review),status:b.dataset.status,review_note:'رسیدگی در پنل دمو',request_id:crypto.randomUUID()};else body={product_id:b.dataset.pid,shop_id:b.dataset.shop,event_type:b.dataset.sellerAction,qty:b.dataset.sellerAction==='supply'?1:undefined,request_id:crypto.randomUUID()};const endpoint=b.dataset.review?'/api/seller/review':'/api/inventory/update';const r=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();toast(r.ok?'عملیات ثبت شد.':(d.error||'ثبت نشد.'),r.ok?'ok':'warn');if(r.ok)loadSeller()}catch(_){toast('ثبت عملیات ناموفق بود.','warn')}});

  /* ------------------------------------------------------------ image fallback */
  document.addEventListener('error', e => {
    const img = e.target;
    if (!(img instanceof HTMLImageElement) || img.dataset.fbfix) return;
    img.dataset.fbfix = '1';
    img.classList.add('imgmissing');
    if (img.parentElement && !img.parentElement.querySelector('.imgmissingmark')) {
      img.parentElement.insertAdjacentHTML('beforeend', '<span class="imgmissingmark" aria-hidden="true">◇</span>');
    }
  }, true);

  /* ---------------------------------------------------------------- typeahead */
  const suggestBox = document.getElementById('suggestBox');
  let suggestItems = [];
  let suggestIndex = -1;
  let suggestTimer = 0;
  let suggestSeq = 0;

  function hideSuggest() {
    suggestBox?.classList.add('hidden');
    qInput?.setAttribute('aria-expanded', 'false');
    suggestIndex = -1;
  }

  function paintSuggest(items) {
    if (!suggestBox) return;
    suggestItems = items;
    suggestIndex = -1;
    if (!items.length) { hideSuggest(); return; }
    const glyph = { category: '▤', brand: '◈', product: '◇' };
    suggestBox.innerHTML = items.map((it, i) => `
      <button class="suggestitem" type="button" role="option" data-i="${i}" data-type="${esc(it.type)}">
        <span class="sicon" aria-hidden="true">${glyph[it.type] || '◇'}</span>
        <b>${esc(it.label)}</b>
        <small>${esc(it.hint || '')}</small>
      </button>`).join('');
    suggestBox.classList.remove('hidden');
    qInput?.setAttribute('aria-expanded', 'true');
  }

  function chooseSuggest(item) {
    if (!item || !qInput) return;
    if (item.type === 'category') {
      applyCategory(item.label, { scroll: false });
      hideSuggest();
      return;
    }
    qInput.value = item.value;
    hideSuggest();
    document.getElementById('search')?.click();
  }

  qInput?.addEventListener('input', () => {
    const term = qInput.value.trim();
    clearTimeout(suggestTimer);
    if (term.length < 2) { hideSuggest(); return; }
    suggestTimer = setTimeout(async () => {
      const seq = ++suggestSeq;
      try {
        const r = await fetch('/api/suggest?q=' + encodeURIComponent(term));
        const d = await r.json();
        if (seq !== suggestSeq) return;           // a newer keystroke already won
        paintSuggest((d.items || []).slice(0, 8));
      } catch (e) { /* typeahead is optional; the form still works */ }
    }, 180);
  });

  qInput?.addEventListener('keydown', e => {
    const open = suggestBox && !suggestBox.classList.contains('hidden') && suggestItems.length;
    if (e.key === 'ArrowDown' && open) {
      e.preventDefault();
      suggestIndex = (suggestIndex + 1) % suggestItems.length;
    } else if (e.key === 'ArrowUp' && open) {
      e.preventDefault();
      suggestIndex = (suggestIndex - 1 + suggestItems.length) % suggestItems.length;
    } else if (e.key === 'Escape') {
      hideSuggest(); return;
    } else if (e.key === 'Enter' && open && suggestIndex >= 0) {
      e.preventDefault(); e.stopPropagation();
      chooseSuggest(suggestItems[suggestIndex]); return;
    } else {
      return;
    }
    suggestBox.querySelectorAll('.suggestitem').forEach((el, i) => {
      el.classList.toggle('is-hot', i === suggestIndex);
      if (i === suggestIndex) el.scrollIntoView({ block: 'nearest' });
    });
  });

  suggestBox?.addEventListener('click', e => {
    const b = e.target.closest('.suggestitem');
    if (!b) return;
    chooseSuggest(suggestItems[Number(b.dataset.i)]);
  });

  document.addEventListener('click', e => {
    if (!e.target.closest('#searchBox')) hideSuggest();
  });

  /* ----------------------------------------------------------------- filters */
  const filterToggle = document.getElementById('filterToggle');
  const filterGroups = document.getElementById('filterGroups');
  filterToggle?.addEventListener('click', () => {
    const open = filterToggle.getAttribute('aria-expanded') !== 'false';
    filterToggle.setAttribute('aria-expanded', String(!open));
    filterGroups?.classList.toggle('collapsed', open);
  });

  // On small screens the detail filters start collapsed so the fold belongs
  // to search + results, not to 30 checkboxes.
  if (window.matchMedia('(max-width: 700px)').matches && filterToggle && filterGroups) {
    filterToggle.setAttribute('aria-expanded', 'false');
    filterGroups.classList.add('collapsed');
  }

  /* ------------------------------------------------------------- spotlight list */
  // Fills the discovery rail with real snapshot products instead of a blank box.
  async function loadSpotlight() {
    const host = document.getElementById('spotlightList');
    if (!host) return;
    try {
      const r = await fetch('/api/search?q=&limit=3&sort=newest');
      const d = await r.json();
      const items = (d.items || []).slice(0, 3);
      host.innerHTML = items.map(p => {
        const img = (p.image_urls || [])[0];
        const title = esc(p.persian_title || 'محصول');
        return `<button class="spotitem" type="button" data-spot="${esc(p.product_id)}">
            ${img ? `<img src="${esc(img)}" alt="" loading="lazy">` : '<span class="spotitem-ph">◇</span>'}
            <span class="spotbody"><b>${title}</b>
              <small>${esc(p.brand_name || 'برند نامعلوم')}${p.min_price != null ? ' · از ' + fa(p.min_price) + ' تومان' : ''}</small>
            </span>
          </button>`;
      }).join('');
    } catch (e) { host.innerHTML = ''; }
  }
  document.getElementById('spotlightList')?.addEventListener('click', e => {
    const b = e.target.closest('[data-spot]');
    if (b && typeof window.openProduct === 'function') window.openProduct(b.dataset.spot);
  });

  /* --------------------------------------------------------------- data modal */
  // The header button and the drawer share one dialog; Escape and backdrop close it.
  const modal = document.getElementById('modal');
  function openStats() { document.getElementById('metaBtn')?.click(); }
  document.getElementById('drawerMetaBtn')?.addEventListener('click', () => { closeDrawer(); openStats(); });
  modal?.addEventListener('click', e => { if (e.target === modal) modal.classList.add('hidden'); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) modal.classList.add('hidden');
  });

  /* ---------------------------------------------------- reveal-on-scroll polish */
  if (motionOK && 'IntersectionObserver' in window) {
    const targets = [...document.querySelectorAll('.insight, .chatbox, .results .sectionhead, .planbody, .railcard')];
    targets.forEach(t => t.classList.add('reveal'));
    document.body.classList.add('motion-ready');
    const io = new IntersectionObserver(entries => {
      entries.forEach(en => {
        if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); }
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
    targets.forEach(t => io.observe(t));
  }

  /* ---------------------------------------------------------------- bootstrap */
  function boot() {
    loadCategories();
    loadSpotlight();
    onScroll();
    setTimeout(spy, 120);
    setTimeout(syncStrip, 200);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  // Expose the handful of helpers other modules/tests may need.
  window.TorobShell = {
    openNotify, toast, focusSearch, applyCategory, refreshCategories: loadCategories,
    suggestionActive: () => !!(suggestBox && !suggestBox.classList.contains('hidden') && suggestIndex >= 0),
  };
})();
