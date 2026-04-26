// En producción (Railway) el frontend se sirve desde el mismo origen que la API → URL relativa.
// En local el frontend corre en :3000 y la API en :8000 → URL absoluta.
window.API = (window.location.hostname === 'localhost' && window.location.port !== '8000')
  ? 'http://localhost:8000'
  : '';

window.api = {
  _h() {
    const t = localStorage.getItem('jwt_token');
    const h = { 'Content-Type': 'application/json' };
    if (t) h['Authorization'] = 'Bearer ' + t;
    return h;
  },
  async _handle(r) {
    if (r.status === 401) {
      localStorage.removeItem('jwt_token');
      window.location.href = '01-login.html';
      return;
    }
    if (!r.ok) throw new Error(r.statusText);
    return r.json();
  },
  async get(path)       { return this._handle(await fetch(API+path, {headers:this._h()})); },
  async post(path,body) { return this._handle(await fetch(API+path, {method:'POST',  headers:this._h(), body:JSON.stringify(body||{})})); },
  async put(path,body)  { return this._handle(await fetch(API+path, {method:'PUT',   headers:this._h(), body:JSON.stringify(body||{})})); },
  async del(path)       { return this._handle(await fetch(API+path, {method:'DELETE', headers:this._h()})); },
};

window.session = {
  get agentId()    { return localStorage.getItem('mockup_agent_id')   || 'ag-ana'; },
  set agentId(v)   { localStorage.setItem('mockup_agent_id', v); },
  get agentName()  { return localStorage.getItem('mockup_agent_name') || 'Ana López'; },
  set agentName(v) { localStorage.setItem('mockup_agent_name', v); },
  get lastCaseId() { return localStorage.getItem('mockup_last_case'); },
  set lastCaseId(v){ localStorage.setItem('mockup_last_case', v); },
  get token()      { return localStorage.getItem('jwt_token'); },
  logout() {
    localStorage.removeItem('jwt_token');
    localStorage.removeItem('mockup_agent_id');
    localStorage.removeItem('mockup_agent_name');
    window.location.href = '01-login.html';
  },
};

// Redirigir a login si no hay token (excepto en la propia página de login)
(function checkAuth() {
  const isLogin = window.location.pathname.includes('01-login');
  if (!isLogin && !localStorage.getItem('jwt_token')) {
    window.location.href = '01-login.html';
  }
}());

window.fmt = {
  date(iso){
    if(!iso) return '';
    const d = new Date(iso+'Z');
    const now = new Date();
    const diff = (now-d)/1000;
    if(diff < 60) return 'ahora';
    if(diff < 3600) return Math.floor(diff/60)+'m';
    if(diff < 86400) return d.toLocaleTimeString('es-AR',{hour:'2-digit',minute:'2-digit'});
    return d.toLocaleDateString('es-AR',{day:'2-digit',month:'short'});
  },
  channelIcon(c){
    return ({
      whatsapp:'<span class="text-green-500" title="WhatsApp">●</span>',
      email:'<span class="text-blue-500" title="Email">✉</span>',
      instagram:'<span class="text-pink-500" title="Instagram">◆</span>',
      telegram:'<span class="text-sky-500" title="Telegram">▲</span>',
    })[c]||'<span class="text-slate-400">●</span>';
  },
  channelLabel(c){
    return ({whatsapp:'WhatsApp',email:'Email',instagram:'Instagram',telegram:'Telegram'})[c]||c;
  },
  urgencyBadge(u){
    return u==='alta'
      ? '<span class="text-[10px] bg-red-100 text-red-700 px-1.5 py-0.5 rounded-full font-medium">Urgente</span>'
      : '';
  },
  stageBadge(s){
    const map = {consulta:'slate',cotizacion:'blue',reserva_tentativa:'amber',sena:'amber',pago_total:'green',documentacion:'purple',pre_viaje:'indigo',en_viaje:'teal',post_viaje:'gray'};
    const c = map[s]||'slate';
    const label = (s||'').replace(/_/g,' ');
    return `<span class="text-[10px] bg-${c}-100 text-${c}-700 px-1.5 py-0.5 rounded-full font-medium capitalize">${label}</span>`;
  },
  avatar(name){
    const initials = (name||'?').split(' ').slice(0,2).map(w=>w[0]).join('').toUpperCase();
    const colors = ['bg-blue-500','bg-purple-500','bg-green-600','bg-orange-500','bg-teal-500'];
    const idx = (name||'').charCodeAt(0) % colors.length;
    return `<div class="w-9 h-9 rounded-full ${colors[idx]} flex items-center justify-center text-white text-sm font-semibold flex-shrink-0">${initials}</div>`;
  },
};

// Sidebar de navegación compartido (íconos a la izquierda)
window.ui = {
  sidebar(active){
    const items = [
      {href:'02-inbox.html', icon:'M2 5a2 2 0 012-2h8a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V5zm3 1h6v4H5V6zm6 6H5v2h6v-2z M15 7a2 2 0 012-2h2a2 2 0 012 2v10a2 2 0 01-2 2h-2a2 2 0 01-2-2V7z', label:'Inbox', id:'inbox'},
      {href:'04-a-seguir.html', icon:'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4', label:'A seguir', id:'aseguir'},
      {href:'06-crm-agencia.html', icon:'M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4', label:'CRM', id:'crm'},
      {href:'07-pipeline.html', icon:'M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7m0 10a2 2 0 002 2h2a2 2 0 002-2V7a2 2 0 00-2-2h-2a2 2 0 00-2 2', label:'Pipeline', id:'pipeline'},
      {href:'13-dashboard.html', icon:'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z', label:'Dashboard', id:'dashboard'},
      {href:'05-mi-perfil.html', icon:'M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z', label:'Mi perfil', id:'perfil'},
      {href:'14-settings.html', icon:'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z', label:'Settings', id:'settings'},
      {href:'index.html', icon:'M4 6h16M4 12h16M4 18h16', label:'Más', id:'mas'},
    ];
    return `<nav class="w-14 bg-slate-900 flex flex-col items-center py-3 gap-1 flex-shrink-0 h-screen sticky top-0">
      <a href="index.html" class="mb-3 w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center text-white font-bold text-sm">P</a>
      ${items.map(it=>`
        <a href="${it.href}" title="${it.label}" class="w-10 h-10 rounded-xl flex items-center justify-center transition-colors ${active===it.id?'bg-slate-700 text-white':'text-slate-400 hover:text-white hover:bg-slate-800'}">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="${it.icon}"/></svg>
        </a>`).join('')}
      <div class="flex-1"></div>
      <div class="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center text-white text-xs font-semibold cursor-pointer" title="${session.agentName} · Cerrar sesión" onclick="session.logout()">${(session.agentName||'A')[0]}</div>
    </nav>`;
  },
  async wireHealth(elId='health-pill'){
    const el = document.getElementById(elId);
    if(!el) return;
    try{
      const h = await api.get('/health');
      el.textContent = `IA ${h.ai_real?'real':'mock'} · Tourbo ${h.tourbo_configured?'on':'mock'}`;
      el.className='text-xs text-slate-500';
    }catch(e){
      el.textContent='backend OFF — levantá uvicorn';
      el.className='text-xs text-red-600 font-semibold';
    }
  },
};

function escapeHtml(s){ return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
window.escapeHtml = escapeHtml;
