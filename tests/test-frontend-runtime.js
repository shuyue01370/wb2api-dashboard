// 真实运行 index.html 的内联脚本（Node 22 自带 fetch，数据取自正在运行的面板服务）
// 目的：抓运行时异常 + 核对各区域确实渲染出内容
const fs = require('fs');
const vm = require('vm');

const BASE = 'http://127.0.0.1:7864';
const html = fs.readFileSync('D:\\utils\\wb2api-dashboard\\index.html', 'utf8');
const code = html.match(/<script>([\s\S]*?)<\/script>/)[1];

let fail = 0;
const ok = (c, m) => { console.log((c ? '  OK   ' : '  FAIL ') + m); if (!c) fail++; };

/* ---------------- DOM 桩 ---------------- */
const errors = [];
function mkEl(key) {
  const el = {
    _key: key, _html: '', textContent: '', value: '', checked: false,
    style: {}, scrollTop: 0, scrollHeight: 100, offsetWidth: 1,
    className: '', options: [], children: [],
    classList: { add() {}, remove() {}, contains() { return false } },
    getAttribute() { return null }, setAttribute() {}, hasAttribute() { return false },
    removeAttribute() {}, addEventListener() {}, appendChild(c) { this.children.push(c) },
    remove() {}, removeChild() {}, closest() { return mkEl(key + '>closest') },
    querySelector(sel) { return getEl(key + sel) },
    querySelectorAll() { return [] }, select() {}, focus() {},
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return this._html },
    set(v) { this._html = String(v) },
  });
  return el;
}
const registry = new Map();
function getEl(key) { if (!registry.has(key)) registry.set(key, mkEl(key)); return registry.get(key) }

const doc = {
  documentElement: mkEl(':root'),
  body: mkEl(':body'),
  createElement: (t) => mkEl(':created:' + t),
  getElementById: (id) => getEl('#' + id),
  querySelector: (s) => getEl(s),
  querySelectorAll: () => [],
  addEventListener() {},
};

const localStore = {};
// 浏览器里相对 URL 会按页面 origin 解析；Node 的 fetch 不会，这里补上（否则整页拿不到数据）
const realFetch = globalThis.fetch;
const sandboxFetch = (u, opt) => realFetch(/^https?:/.test(u) ? u : BASE + u, opt);
const sandbox = {
  console, JSON, Math, Date, Number, String, Boolean, Array, Object, RegExp, Error,
  isNaN, parseInt, parseFloat, encodeURIComponent, decodeURIComponent, Promise, Set, Map,
  setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {},
  fetch: sandboxFetch, AbortController,
  document: doc,
  window: { addEventListener() {} },
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  localStorage: {
    getItem: (k) => (k in localStore ? localStore[k] : null),
    setItem: (k, v) => { localStore[k] = String(v) },
  },
  confirm: () => true,
  alert: () => {},
};
sandbox.globalThis = sandbox;
sandbox.window.document = doc;
vm.createContext(sandbox);

process.on('uncaughtException', (e) => errors.push('uncaught: ' + e.message));
process.on('unhandledRejection', (e) => errors.push('unhandled: ' + (e && e.message || e)));

/* ---------------- 执行 ---------------- */
try {
  new vm.Script(code, { filename: 'dashboard.js' }).runInContext(sandbox);
  ok(true, '整页脚本在 DOM 桩中执行完成（无同步异常）');
} catch (e) {
  ok(false, '脚本执行抛异常：' + e.message);
  process.exit(1);
}

/* ---------------- 等真实数据回来 ---------------- */
const wait = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  // boot 时 refreshAll 会并行打 6 个接口；给足时间
  await wait(6000);
  // 再手动触发一次刷新（模拟用户点「刷新」路径）
  try {
    await sandbox.eval('(function(){return (typeof refreshAll==="function")?refreshAll(true):null})()');
  } catch (e) { /* refreshAll 在 IIFE 内，外部拿不到属正常 */ }
  await wait(2500);

  const H = (key) => (getEl(key)._html || '');
  const has = (key, needle, label) => ok(H(key).includes(needle), label + ' → 含「' + needle + '」');

  // 账号池规模。干净环境（新用户首次运行 / 便携数据根）账号池为空，
  // 此时账号相关断言应校验「空态」而不是拿本机数据去匹配。
  let poolCount = 0;
  try {
    const st0 = await sandboxFetch(BASE + '/api/status').then(r => r.json());
    poolCount = Array.isArray(st0.accounts) ? st0.accounts.length : (st0.total || 0);
  } catch (e) { /* 接口不可用按空池处理 */ }
  console.log('  （账号池账号数 = ' + poolCount + '）');

  console.log('  ---- 渲染产物核对 ----');
  has('#kpis', '账号总数', 'KPI 区');
  has('#kpis', '可服务', 'KPI 区');
  has('#kpis', '存储模式', 'KPI 区');
  has('#acts', '一键每日签到', '动作区');
  has('#acts', '成长计划任务', '动作区');
  ok(!H('#acts').includes('网关连通性测试'),
     '动作区已移除重复的连通性测试（统一收进「接口测试」页签）');
  has('#acts', 'curl.exe -s http://localhost:', '动作区显示了那条 curl 命令');

  console.log('  ---- 成长计划：一键接取 / 一键领奖 ----');
  ok(H('#acts').includes('data-act="tasks_all"'), '存在聚合动作 tasks_all');
  ['accept', 'claim'].forEach(seg =>
    ok(H('#acts').includes('data-only="' + seg + '"'), '按钮：data-only="' + seg + '"'));
  ok(H('#acts').includes('一键接取所有任务'), '按钮文案：一键接取所有任务');
  ok(H('#acts').includes('一键领取所有积分奖励'), '按钮文案：一键领取所有积分奖励');
  ok(!H('#acts').includes('data-only="run"'), '面板不暴露补跑入口');
  ok(!H('#acts').includes('id="taskSel"'), '单任务下拉已移除');
  ok(H('#acts').includes('id="taskDry"'), '存在「仅预览」开关');
  ok(H('#taskNote').includes('点击即执行'), '默认（未勾预览）提示点击即执行');
  ok(!H('#taskNote').includes('undefined'), '提示文案无 undefined 污染');
  // #taskCmd 是用 textContent 赋值的（避免转义问题），所以这里读 textContent 而不是 innerHTML
  let META = {};
  try { META = await sandboxFetch(BASE + '/api/meta').then(r => r.json()); } catch (e) { /* ignore */ }
  const isNative = META.mode === 'native';
  const taskCmd = getEl('#taskCmd').textContent || '';
  ok(taskCmd.includes('tasks_all.py'), '命令展示指向聚合脚本');
  ok(taskCmd.includes('--yes'), '默认真实执行（命令含 --yes）');
  if (isNative) {
    ok(taskCmd.includes('WB2API_AUTHS'), 'native：命令展示含 WB2API_AUTHS 环境变量注入');
    ok(taskCmd.includes('WB2API_SCRIPTS'), 'native：命令展示含 WB2API_SCRIPTS 环境变量注入');
    ok(!taskCmd.includes('docker'), 'native：命令展示不含 docker');
  } else {
    ok(taskCmd.includes('/root/workbuddy2api/auths'), 'docker：命令展示含 auths 挂载');
    ok(taskCmd.includes('--user root'), 'docker：命令展示含 --user root（容器默认用户读不到 auths）');
  }
  has('#accHead', '状态', '账号表头');
  if (poolCount > 0) {
    has('#accBody', 'shuyue', '账号表已渲染真实账号');
    has('#accBody', '健康', '账号状态徽标');
  } else {
    ok(H('#accBody').length > 0, '空账号池下账号表渲染了空态（而非空白）');
  }
  has('#pickSim', '可见权重', '选号模拟区（权重说明）');
  has('#pickSim', '模拟抽签', '选号模拟区（抽签按钮）');
  has('#chain', '① 直接选号', '降级链路第 1 级');
  has('#chain', '② 模型级豁免', '降级链路第 2 级');
  has('#chain', '③ 全冷却兜底', '降级链路第 3 级');
  has('#chain', '④ 彻底耗尽', '降级链路第 4 级');
  has('#failoverExtra', 'fallback_earliest_expiry', '切换事件说明');
  has('#failoverExtra', '连续 3 次 12153', '禁用规则说明');
  has('#sched', '签到 + 余额解冻', '排程区');
  has('#sched', 'Token 保活', '排程区');
  has('#cfg', '软冷却基数', '配置区');
  has('#cfg', '熔断阈值', '配置区');
  // 模型列表来自实时接口，断言用真实返回的第一个 id，避免写死一个上游可能不存在的模型名
  let liveIds = [];
  try {
    const mr = await sandboxFetch(BASE + '/api/models').then(r => r.json());
    liveIds = (mr.data || []).map(m => m.id);
  } catch (e) { /* ignore */ }
  ok(liveIds.length > 0 && H('#models').includes(liveIds[0]),
     '模型列表含实时接口返回的 ' + liveIds[0] + '（共 ' + liveIds.length + ' 个）');
  ok(H('#models').includes('共 <b>' + liveIds.length + '</b> 个模型'), '模型数量与接口一致');
  has('#apiHelp', '/v1/chat/completions', '接口示例');
  has('#apiHelp', 'Authorization: Bearer', '接口示例含鉴权头');
  if (poolCount > 0) {
    has('#files', 'workbuddy-', '账号文件区');
  } else {
    ok(H('#files').length > 0, '空账号池下账号文件区渲染了空态（而非空白）');
  }
  if (isNative) {
    has('#files', '原生进程（无 Docker）', '运行形态区（native）');
    has('#files', 'PID', '运行形态区显示进程 PID');
  } else {
    has('#files', '重启次数', '容器区');
  }
  has('#tut', '三条铁律', '教程区：铁律');
  has('#tut', 'claude-code-router', '教程区：Claude Code 中转方案');
  has('#tut', 'api_base_url', '教程区：CCR 配置字段');
  has('#tut', 'ANTHROPIC_BASE_URL', '教程区：Claude Code 环境变量');
  has('#tut', 'wire_api', '教程区：Codex 配置字段');
  has('#tut', 'transformer', '教程区：CCR transformer');
  has('#tut', 'passthrough', '教程区：prompt.mode 说明');
  has('#tut', 'OPENAI_API_BASE', '教程区：Aider 配置');
  has('#tut', 'apiBase', '教程区：Continue 配置');
  has('#tut', 'http://127.0.0.1:', '教程区引用了本机实际网关地址');

  console.log('  ---- 页签结构 ----');
  ['概览', '账号池', '自动切换', '运维操作', '接口测试', '接入教程'].forEach(n =>
    has('#mainTabs', n, '页签栏含'));
  ok(H('#mainTabs').includes('class="tabbtn on"'), '页签栏有选中态');
  ok(getEl('#pane-overview').className === 'pane on', '启动后默认显示「概览」页签');
  ['pool', 'failover', 'ops', 'api', 'tut'].forEach(p =>
    ok(getEl('#pane-' + p).className === 'pane',
       '非当前页签 #pane-' + p + ' 处于隐藏态（display:none）'));
  ok(localStore['wb2api-tab'] === 'overview', '页签选择已写入 localStorage（下次打开记忆住）');

  console.log('  ---- 接口测试页签 ----');
  ['healthz', 'status', 'models', 'chat'].forEach(ep =>
    ok(H('#apiHelp').includes('data-test="' + ep + '"'), '接口示例带「测试此接口」按钮：' + ep));
  ok(H('#apiHelp').includes('data-copy='), '接口示例仍保留复制按钮');
  ok(H('#apiHelp').includes('id="chatModel"') && H('#apiHelp').includes('id="modelOpts"'),
     '对话测试带模型选择（沿用 fillModelOptions 的 datalist）');
  ok(H('#apiTestOut').includes('测试此接口'), '接口测试结果区有初始引导文案');
  console.log('  ---- 服务端响应格式（任何 API 路径都必须是 JSON，不能是 HTML 错误页）----');
  for (const p of ['/api/__nope__', '/api/job?id=__nope__']) {
    try {
      const r = await sandboxFetch(BASE + p);
      const t = await r.text();
      ok(r.headers.get('content-type').indexOf('application/json') >= 0 && t.trim().startsWith('{'),
         p + ' → HTTP ' + r.status + ' 且响应体是 JSON');
    } catch (e) {
      ok(false, p + ' 请求失败：' + e.message);
    }
  }
  try {
    const pr = await sandboxFetch(BASE + '/api/probe?ep=healthz').then(r => r.json());
    ok(pr && pr.path === '/healthz' && typeof pr.elapsed_ms === 'number',
       '后端探针 /api/probe 可用（healthz → HTTP ' + pr.status + '，' + pr.elapsed_ms + ' ms）');
    ok(typeof pr.preview === 'string' && pr.preview.length > 0, '探针返回了响应体预览');
    const pr2 = await sandboxFetch(BASE + '/api/probe?ep=status').then(r => r.json());
    ok(pr2 && pr2.path === '/status' && (pr2.body && pr2.body.accounts),
       '探针能取到账号池数据（status → ' + ((pr2.body && pr2.body.accounts) || []).length + ' 个账号）');
  } catch (e) {
    ok(false, '探针接口调用失败：' + e.message);
  }
  const tutModel = liveIds.find(id => H('#tut').includes(id));
  ok(!!tutModel, '教程区配置示例用的是实时模型名（命中 ' + tutModel + '）');
  ok(H('#tut').includes('复制'), '教程区各配置块带复制按钮');

  console.log('  ---- 附加校验 ----');
  // 任何容器都不应出现 JS 的 undefined / NaN，或 class="xxx undefined" 这种拼接污染
  ['#kpis', '#acts', '#accBody', '#pickSim', '#chain', '#sched', '#cfg', '#files', '#apiHelp',
   '#tut', '#mainTabs', '#apiTestOut', '#taskNote', '#taskCmd', '#ccswitch']
    .forEach((sel) => {
      ok(!/undefined/.test(H(sel)), sel + ' 未出现 undefined');
      ok(!/NaN/.test(H(sel)), sel + ' 未出现 NaN');
    });
  ok(!/class="[^"]*\bundefined\b/.test(html.replace(/<script>[\s\S]*?<\/script>/, '') + H('#kpis') + H('#chain')),
     '渲染出的 class 属性无 undefined 污染');
  ok(!/0001-01-01/.test(H('#accBody')), '账号表未渲染 Go 零值时间');
  // 千分位断言不写死数值（积分随账号使用变化，写死会变假失败）：
  // 规则 = 出现 ≥1000 的值就必须带千分位逗号
  var numCells = (H('#accBody').match(/<td class="num">[\d,]+<\/td>/g) || []).join('');
  ok(poolCount === 0 || numCells.length > 0, '账号表数字单元格已渲染（.num）');
  ok(/,\d{3}/.test(numCells) || !/\d{4,}/.test(numCells.replace(/,/g, '')),
     '积分千分位：≥1000 的值带逗号分隔');
  ok(poolCount === 0 || H('#accBody').includes('data-pool-remove='),
     '账号池每行带「移除」按钮（data-pool-remove）');
  ok(H('#accHead').includes('操作'), '账号池表头含「操作」列');
  ok(poolCount > 0 || H('#accBody').includes('colspan="13"'),
     '账号池空态的 colspan 与列数一致');

  console.log('  ---- 本机客户端账号 ----');
  const lac = H('#localAccounts');
  ok(lac.length > 0, '本机客户端区块已渲染');
  ok(lac.includes('已在账号池') || lac.includes('未入池') || lac.includes('拉黑'),
     '渲染了入池状态（已在账号池 / 未入池；全部拉黑时为拉黑提示）');
  ok(!lac.includes('undefined') && !lac.includes('NaN'), '本机客户端区块无 undefined / NaN');
  ok(String(getEl('#localTag').textContent || '').length > 0, '区块角标 #localTag 有内容');
  ok(lac.includes('data-import='), '存在抓取入池按钮（data-import）');
  ok(lac.includes('抓取全部入池'), '存在「抓取全部入池」按钮');
  ok(lac.includes('data-block=') || !lac.includes('data-import='),
     '本机账号每行带「拉黑」按钮（data-block）');
  ok(lac.includes('重新登录'), '拉黑说明文案齐全（重新登录后自动恢复）');
  ok(!lac.includes('eyJ'), '区块不渲染 token 明文（脱敏）');
  if (lac.includes('未入池')) {
    ok(lac.includes('抓取入池') || lac.includes('发起授权'), '未入池账号有对应动作按钮');
  } else {
    ok(true, '当前账号均已在池（动作按钮断言跳过）');
  }

  console.log('  ---- OAuth 授权弹窗 ----');
  ok(html.includes('id="loginMask"'), '授权弹窗 DOM 已内置');
  ok(html.includes('id="loginUrl"') && html.includes('id="loginWait"'), '弹窗含授权地址框与等待条');
  ok(html.includes('data-login-open'), '「发起授权」入口已接线（打开弹窗）');
  ok(html.includes('id="ltab-token"') && html.includes('id="ltab-local"'), '弹窗三标签页齐全');
  ok(html.includes('data-token-add'), 'Token 粘贴添加入口存在');
  ok(code.includes('loginPollStop') && code.includes('loginModalClose'), '关窗即停止轮询的逻辑存在');

  console.log('  ---- 错误提示渲染（[object Object] 回归）----');
  // 网关的错误体是对象：{"error":{"code":"invalid_api_key","message":"..."}}
  // 曾经 showAlert 直接 esc(o.error) → 每个页签顶部都挂着一条 [object Object]
  const T = sandbox.window.__wb2apiTest;
  ok(!!T && typeof T.showAlert === 'function', '存在测试钩子 window.__wb2apiTest.showAlert');
  if (T) {
    T.showAlert({ error: { code: 'invalid_api_key', message: 'missing or invalid API key' } });
    const box = H('#alertBox');
    ok(box.indexOf('[object Object]') < 0, '对象型错误不再渲染成 [object Object]');
    ok(box.includes('missing or invalid API key') || box.includes('invalid_api_key'),
       '对象型错误渲染出了可读信息（message / code）');
    ok(box.includes('api_key 不一致'), '401 场景附带了可操作的提示文案');

    T.showAlert({ error: '普通字符串错误' });
    ok(H('#alertBox').includes('普通字符串错误'), '字符串型错误正常显示');

    T.showAlert({ error: { code: 'x', nested: { a: 1 } } });
    ok(H('#alertBox').indexOf('[object Object]') < 0, '无 message 的对象也只输出 JSON，不出 [object Object]');

    T.hideAlert();
    ok(H('#alertBox') === '', 'hideAlert 能清空提示条');
    ok(String(T.errText(null)) === '' && String(T.errText('a')) === 'a', 'errText 对 null / 字符串的处理正确');
  }

  console.log('  ---- 本机账号拉黑：前端过滤渲染 ----');
  // 注入一个「被拉黑」的登录态条目，验证它不再出现在列表里、且提供手动解除入口。
  // 只改内存里的 S.meta，不调任何接口（绝不改动真实拉黑名单）。
  if (T && T.state && T.renderLocalAccounts) {
    const S = T.state;
    const snap = JSON.stringify((S.meta && S.meta.desktop_logins) || null);
    const snapFlag = S.showBlocked;
    try {
      const dl = JSON.parse(snap || '{}');
      dl.accounts = dl.accounts || [];
      if (dl.accounts.length) {
        const victim = dl.accounts[0];
        const short = String(victim.uid).slice(0, 8);
        dl.blocked = [{ uid: victim.uid, nickname: victim.nickname || '', blocked_at: 1790000000 }];
        S.meta.desktop_logins = dl;
        S.showBlocked = false;
        T.renderLocalAccounts();
        const after = H('#localAccounts');
        ok(after.indexOf(short) < 0, '被拉黑的账号不再渲染在列表中（' + short + '）');
        ok(after.includes('已拉黑 1 个'), '角标/按钮提示了「已拉黑 1 个」');
        S.showBlocked = true;
        T.renderLocalAccounts();
        const shown = H('#localAccounts');
        ok(shown.includes('data-unblock='), '展开「已拉黑」名单后有「解除」按钮');
        ok(shown.includes(short) || (victim.nickname && shown.includes(victim.nickname)),
           '已拉黑名单里能看到该账号');
      } else {
        ok(true, '当前无本机账号数据（拉黑过滤断言跳过）');
      }
    } finally {
      S.meta.desktop_logins = JSON.parse(snap || '{}');
      S.showBlocked = snapFlag;
      T.renderLocalAccounts();
    }
    ok(!H('#localAccounts').includes('[object Object]'), '恢复正常后区块无 [object Object]');
  } else {
    ok(false, '测试钩子缺少 renderLocalAccounts / state，无法验证拉黑过滤');
  }

  console.log('  ---- 一键导入 cc-switch ----');
  let apiPort = 0, metaCs = {};
  try {
    const m = await sandboxFetch(BASE + '/api/meta').then(r => r.json());
    apiPort = m.api_port;
    metaCs = m.ccswitch || {};
  } catch (e) {}
  // 深链要等 /v1/models 回来才会生成（模型名以实时列表为准，拿不到就不给导入）——
  // 这里等它就绪，最多 10 秒
  for (let i = 0; i < 20 && !H('#ccswitch').includes('ccswitch://'); i++) {
    await new Promise(r => setTimeout(r, 500));
  }
  const cc = H('#ccswitch');
  ok(cc.length > 0, 'cc-switch 区块已渲染');
  if (metaCs.registered) {
    ok(cc.includes('已注册') && cc.includes(metaCs.exe),
       '已注册时显示实测到的处理程序路径（' + metaCs.exe + '）');
  } else {
    ok(cc.includes('未检测到'), '未检测到协议时给出明确提示');
  }
  ok(!cc.includes('HKEY_CLASSES_ROOT'), '前端不再写死注册表位置（改由后端实测）');
  ok(html.includes('id="dlgMask"'), '通用提示弹窗 DOM 已内置（唤起失败时用）');
  ok(cc.includes('ccswitch://v1/import?'), '生成了 ccswitch:// 深链');
  ok(cc.includes('app=claude'), '含 Claude Code 深链（app=claude）');
  ok(cc.includes('app=codex'), '含 Codex 深链（app=codex）');
  ok(cc.includes('resource=provider'), '深链 resource=provider');
  ok(apiPort > 0 && cc.includes(encodeURIComponent('http://127.0.0.1:' + apiPort + '/v1')),
     '深链 endpoint 指向本机网关（127.0.0.1:' + apiPort + '/v1）');
  ok(cc.includes('apiFormat=openai_chat'), '深链带 apiFormat=openai_chat');
  ok(cc.includes(encodeURIComponent('deepseek-v4.1-flash[1m]')) ||
     /\[1m\]|%5B1m%5D/.test(cc), 'sonnet/opus 模型名带 [1m] 后缀（1M 上下文开关）');
  ok(cc.includes(encodeURIComponent('WorkBuddy2API 本地网关')), '深链带供应商名称');
  ok(cc.includes('data-act="ccswitch"') && cc.includes('data-app="claude"') && cc.includes('data-app="codex"'),
     '两个导入按钮都在');
  ok(cc.includes('15721'), '说明了本地路由端口 15721');
  ok(cc.includes('data-act="ccswitch"') && !cc.includes('data-copy=""'),
     '深链就绪时复制按钮不指向空串');

  console.log('  ---- 按钮反馈 ----');
  ok(getEl('#actStatus') !== undefined, '存在 #actStatus 状态条');
  ok((H('#actStatus') || '') === '', '启动后状态条为空（无残留的「执行中」）');
  ok((getEl('#actStatus').className || '') === '', '状态条初始无 run/ok/bad 类');
  ok(!/class="spin"/.test(H('#acts')), '启动后没有按钮卡在转圈状态');

  console.log('  ---- 运行时异常 ----');
  ok(errors.length === 0, '无 uncaught / unhandled 异常' + (errors.length ? '：' + errors.join(' | ') : ''));

  console.log('\n' + (fail === 0 ? '=== 全部通过 ===' : '=== ' + fail + ' 项失败 ==='));
  process.exit(fail === 0 ? 0 : 1);
})();
