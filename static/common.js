// Shared behaviour for every IntrepidVan page: the hub password, the safety alert bar
// and tap-to-enlarge for the diagrams. Held in one file so the Pico stores it
// once and the browser caches it across pages.

// ---- the hub password ------------------------------------------------------
// Things that change the van (settings, the heater, the battery...) can be put
// behind a password on the hub. This page never keeps the password: it works
// out a key from it (SHA-256, done here because browsers only offer theirs to
// https pages, and the hub serves http) and keeps that, sending it with every
// request to the hub. When the hub says a request needs the password, a box
// asks for it and the request goes again - no page has to know about any of it.
(function(){
  const K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  function sha256hex(str){
    const b = Array.from(new TextEncoder().encode(str)), n = b.length * 8;
    b.push(0x80); while(b.length % 64 !== 56) b.push(0);
    for(let i = 7; i >= 0; i--) b.push(i > 3 ? 0 : (n >>> (i * 8)) & 255);
    const H = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    const r = (x, s) => (x >>> s) | (x << (32 - s)), W = new Array(64);
    for(let o = 0; o < b.length; o += 64){
      for(let i = 0; i < 16; i++) W[i] = (b[o+4*i] << 24) | (b[o+4*i+1] << 16) | (b[o+4*i+2] << 8) | b[o+4*i+3];
      for(let i = 16; i < 64; i++){
        const s0 = r(W[i-15], 7) ^ r(W[i-15], 18) ^ (W[i-15] >>> 3), s1 = r(W[i-2], 17) ^ r(W[i-2], 19) ^ (W[i-2] >>> 10);
        W[i] = (W[i-16] + s0 + W[i-7] + s1) | 0;
      }
      let [a, bb, c, d, e, f, g, h] = H;
      for(let i = 0; i < 64; i++){
        const t1 = (h + (r(e, 6) ^ r(e, 11) ^ r(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + W[i]) | 0;
        const t2 = ((r(a, 2) ^ r(a, 13) ^ r(a, 22)) + ((a & bb) ^ (a & c) ^ (bb & c))) | 0;
        h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = bb; bb = a; a = (t1 + t2) | 0;
      }
      H[0] = (H[0] + a) | 0; H[1] = (H[1] + bb) | 0; H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
      H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0; H[6] = (H[6] + g) | 0; H[7] = (H[7] + h) | 0;
    }
    return H.map(x => (x >>> 0).toString(16).padStart(8, "0")).join("");
  }
  const KEY = "hubkey";
  window.hubKeyFor = pw => sha256hex("camperdash-hub:" + pw);
  window.hubKey = () => { try{ return localStorage.getItem(KEY) || localStorage.getItem("xtoken") || ""; }catch(e){ return ""; } };
  window.hubSetKey = k => { try{ if(k) localStorage.setItem(KEY, k); else localStorage.removeItem(KEY); localStorage.removeItem("xtoken"); }catch(e){} };

  // the password box: built on the page rather than prompt(), which the
  // Android app's web view does not show
  let asking = null;
  function ask(msg){
    if(asking) return asking;
    asking = new Promise(done => {
      const w = document.createElement("div");
      w.style.cssText = "position:fixed;inset:0;z-index:2000;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;font-family:inherit";
      w.innerHTML = '<form style="background:#1c1a22;color:#efe9e0;border:1px solid #34303c;border-radius:14px;padding:18px;width:min(320px,90vw)">' +
        '<div style="font-weight:700;font-size:17px;margin-bottom:6px">Hub password</div>' +
        '<div style="color:#9a8f9e;font-size:13px;margin-bottom:12px"></div>' +
        '<input type="password" autocomplete="current-password" style="width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid #34303c;background:#27242e;color:#efe9e0;font:inherit">' +
        '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">' +
        '<button type="button" style="padding:9px 14px;border-radius:10px;border:1px solid #34303c;background:#27242e;color:#efe9e0;font:inherit">Cancel</button>' +
        '<button type="submit" style="padding:9px 14px;border-radius:10px;border:0;background:#d3a94a;color:#15131a;font-weight:700;font:inherit">Unlock</button></div></form>';
      w.querySelector("div div:nth-child(2)").textContent = msg;
      const f = w.querySelector("form"), inp = w.querySelector("input");
      const end = v => { w.remove(); asking = null; done(v); };
      f.onsubmit = e => { e.preventDefault(); if(inp.value){ window.hubSetKey(window.hubKeyFor(inp.value)); end(true); } };
      w.querySelector("button[type=button]").onclick = () => end(false);
      document.body.appendChild(w); inp.focus();
    });
    return asking;
  }
  window.hubAsk = ask;

  const F = window.fetch.bind(window);
  window.fetch = async function(url, opt){
    if(typeof url !== "string" || !url.startsWith("/api/")) return F(url, opt);
    const send = () => { const o = Object.assign({}, opt || {}), h = new Headers(o.headers || {}), k = window.hubKey();
      if(k) h.set("X-Token", k); o.headers = h; return F(url, o); };
    let r = await send();
    for(let i = 0; i < 3 && r.status === 403; i++){
      let j = null; try{ j = await r.clone().json(); }catch(e){}
      if(!j || !j.auth) break;
      if(!await ask(i ? "That was not the hub's password. Try again." : "Changing things on the van needs the hub's password.")) break;
      r = await send();
    }
    return r;
  };
})();


// ---- safety alerts ---------------------------------------------------------
// Driven by /api/data so every page shows the same warning. The sound uses the
// Web Audio API rather than an audio file, so there is nothing extra to serve
// from the Pico. Browsers refuse to make noise until the page has been
// interacted with, so the audio is primed on the first tap and the warning is
// always shown visually whether or not sound is allowed.
(function(){
  const bar = document.createElement('div');
  bar.id = 'alertbar';
  bar.innerHTML = '<span class="atext"><span class="atitle"></span>'
                + '<span class="adetail"></span></span>'
                + '<button class="amute" type="button">Clear alarm</button>';
  document.body.appendChild(bar);
  const titleEl = bar.querySelector('.atitle');
  const detailEl = bar.querySelector('.adetail');
  const muteBtn = bar.querySelector('.amute');

  let ctx = null, primed = false, muted = false, current = null, timer = null;
  let clearedId = null, clearedAt = 0;   // cleared here; the hub may not know yet
  let soundAllowed = true;      // set from /api/data; the banner shows regardless

  function prime(){
    if(primed) return;
    try{
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      if(ctx.state === 'suspended') ctx.resume();
      primed = true;
    }catch(e){ primed = true; }   // no audio available: visual only
  }
  addEventListener('pointerdown', prime, {once:true});
  addEventListener('keydown', prime, {once:true});

  function beep(){
    if(!ctx || muted || !soundAllowed) return;
    try{
      const now = ctx.currentTime;
      // two short rising notes - deliberately unlike a notification chime
      [0, 0.26].forEach((off, i) => {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.type = 'square';
        o.frequency.setValueAtTime(i ? 1180 : 880, now + off);
        g.gain.setValueAtTime(0.0001, now + off);
        g.gain.exponentialRampToValueAtTime(0.22, now + off + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, now + off + 0.2);
        o.connect(g).connect(ctx.destination);
        o.start(now + off); o.stop(now + off + 0.22);
      });
    }catch(e){}
  }

  function stop(){
    if(timer){ clearInterval(timer); timer = null; }
    bar.classList.remove('show');
    document.body.classList.remove('alerting');
    document.body.style.paddingTop = '';
    current = null; muted = false;
    muteBtn.textContent = 'Clear alarm'; muteBtn.disabled = false;
  }

  function show(a){
    current = a.id;
    titleEl.textContent = a.title;
    detailEl.textContent = a.detail;
    bar.classList.add('show');
    document.body.classList.add('alerting');
    // push the page down so the banner never covers the header
    document.body.style.paddingTop = (bar.offsetHeight + 8) + 'px';
    // Cleared is the hub's record, shared by every screen: clearing on the van
    // display silences this page too, and the other way round. The banner stays
    // while the condition lasts, so nobody forgets why it went off.
    // a poll that overtakes the Clear request must not start it beeping again
    muted = !!a.acked || (a.id === clearedId && Date.now() - clearedAt < 6000);
    muteBtn.textContent = a.id === 'panic' ? (muted ? 'Stopping\u2026' : 'Stop panic')
                                         : (muted ? 'Cleared' : 'Clear alarm');
    muteBtn.disabled = muted;
    if(muted){ if(timer){ clearInterval(timer); timer = null; } }
    else if(!timer){ beep(); timer = setInterval(beep, 2600); }
  }

  muteBtn.addEventListener('click', async () => {
    muted = true;                          // silent at once, before the hub answers
    clearedId = current; clearedAt = Date.now();
    if(timer){ clearInterval(timer); timer = null; }
    muteBtn.textContent = 'Cleared'; muteBtn.disabled = true;
    try{
      await fetch('/api/alerts/ack', {method:'POST', headers:{'Content-Type':'application/json'},
                                      body: JSON.stringify({id: current})});
    }catch(e){}
  });

  window.showAlerts = function(list){
    const a = (list && list.length) ? list[0] : null;
    if(!a){ if(current) stop(); return; }
    show(a);
  };

  // Every page polls /api/data for its own figures, but the alarm has its own
  // small endpoint, asked every two seconds, so it sounds without waiting for
  // a page's slower refresh - and keeps working if that page errors.
  async function check(){
    try{
      const d = await (await fetch('/api/alerts', {cache:'no-store'})).json();
      if(d.sound !== undefined) soundAllowed = !!d.sound;
      showAlerts(d.alerts);
    }catch(e){}
  }
  check(); setInterval(check, 2000);
})();

// ---- tap a diagram to see it full screen -----------------------------------
// The SVG is MOVED into the overlay rather than copied, so it keeps its ids and
// carries on updating live while enlarged.
(function(){
  const svg = document.querySelector('#scene') || document.querySelector('.wrap svg');
  if(!svg) return;
  const parent = svg.parentNode, next = svg.nextSibling;
  const stage = document.createElement('div'); stage.id = 'zoomstage';
  const bar = document.createElement('div'); bar.id = 'zoombar';
  const rotBtn = document.createElement('button'); rotBtn.textContent = 'Rotate';
  const closeBtn = document.createElement('button'); closeBtn.textContent = 'Close';
  bar.append(rotBtn, closeBtn);
  document.body.append(stage, bar);
  svg.classList.add('zoomable');

  let open = false, rot = false, manual = false;
  const portrait = () => innerHeight > innerWidth;
  const paint = () => stage.classList.toggle('rot', rot);

  function show(){
    if(open) return;
    open = true; manual = false;
    rot = portrait();                 // upright screen: turn the drawing sideways
    stage.appendChild(svg);
    stage.classList.add('open'); bar.classList.add('open');
    document.body.classList.add('zoomed');
    paint();
  }
  function hide(){
    if(!open) return;
    open = false;
    stage.classList.remove('open','rot'); bar.classList.remove('open');
    document.body.classList.remove('zoomed');
    parent.insertBefore(svg, next);   // exactly where it came from
  }

  svg.addEventListener('click', e => { if(open) hide(); else show(); });
  closeBtn.addEventListener('click', hide);
  rotBtn.addEventListener('click', () => { rot = !rot; manual = true; paint(); });
  document.addEventListener('keydown', e => { if(e.key === 'Escape') hide(); });
  // follow the phone being turned, unless the user has chosen for themselves
  addEventListener('resize', () => {
    if(open && !manual){ rot = portrait(); paint(); }
  });
})();
