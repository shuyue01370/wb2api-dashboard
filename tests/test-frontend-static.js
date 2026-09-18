// 前端静态校验：语法 + DOM id 引用完整性 + 关键逻辑单元测试
const fs = require('fs');
const vm = require('vm');
const path = 'D:\\utils\\wb2api-dashboard\\index.html';
const html = fs.readFileSync(path, 'utf8');

let fail = 0;
function ok(cond, msg) {
  console.log((cond ? '  OK   ' : '  FAIL ') + msg);
  if (!cond) fail++;
}

// ---------- 1. 内联 script 语法 ----------
const m = html.match(/<script>([\s\S]*?)<\/script>/);
ok(!!m, '找到内联 script');
const code = m[1];
try {
  new vm.Script(code, { filename: 'inline.js' });
  ok(true, '内联 JS 语法通过（' + code.split('\n').length + ' 行）');
} catch (e) {
  ok(false, '内联 JS 语法错误：' + e.message);
  process.exit(1);
}

// ---------- 2. 标签配对 ----------
[['<html', '</html>'], ['<head>', '</head>'], ['<body>', '</body>'],
 ['<style>', '</style>'], ['<script>', '</script>']].forEach(([a, b]) => {
  const ca = html.split(a).length - 1, cb = html.split(b).length - 1;
  ok(ca === cb, '标签配对 ' + a + ' x' + ca + ' / ' + b + ' x' + cb);
});

// ---------- 3. $("...") 引用的 id 是否都存在 ----------
const refs = new Set();
const re1 = /\$\("#([A-Za-z0-9_\-]+)"\)/g;
let mm;
while ((mm = re1.exec(code))) refs.add(mm[1]);
const re2 = /getElementById\("([A-Za-z0-9_\-]+)"\)/g;
while ((mm = re2.exec(code))) refs.add(mm[1]);
const missing = [...refs].filter(id => !html.includes('id="' + id + '"'));
ok(missing.length === 0, '引用的 ' + refs.size + ' 个 DOM id 全部存在' +
  (missing.length ? '（缺失：' + missing.join(', ') + '）' : ''));

// ---------- 4. 关键组件容器存在 ----------
['kpis', 'acts', 'accBody', 'accHead', 'pickSim', 'chain', 'failoverExtra',
 'evList', 'logView', 'sched', 'cfg', 'models', 'apiHelp', 'apiTestOut', 'tut', 'files',
 'mainTabs', 'pane-overview', 'pane-pool', 'pane-failover', 'pane-ops', 'pane-api',
 'pane-tut', 'actStatus', 'jobArea', 'jobTabs', 'jobOut', 'toast', 'alertBox'].forEach(id => {
  ok(html.includes('id="' + id + '"'), '容器存在 #' + id);
});

// ---------- 4b. 页签结构一致性 ----------
// TABS（页签栏定义）与 PANES（显隐控制）必须一一对应：漏一个就会出现「点了没反应」的页签
const PANES = ['overview', 'pool', 'failover', 'ops', 'api', 'tut'];
const tabsDecl = code.match(/var TABS=\[([\s\S]*?)\];/);
ok(!!tabsDecl, '找到 TABS 定义');
PANES.forEach(p => {
  ok(tabsDecl[1].includes('["' + p + '",'), 'TABS 注册了页签 ' + p);
  ok(html.includes('id="pane-' + p + '"'), '存在对应容器 #pane-' + p);
});
const panesDecl = code.match(/var PANES=\[([^\]]*)\]/);
ok(!!panesDecl, '找到 PANES 定义');
ok(PANES.every(p => panesDecl[1].includes('"' + p + '"')),
  'PANES 覆盖全部 ' + PANES.length + ' 个页签（显隐不会漏项）');
ok(tabsDecl[1].split('["').length - 1 === PANES.length,
  'TABS 条目数与 PANES 数一致（各 ' + PANES.length + ' 个）');
// 每个接口示例都要有对应的测试按钮，否则「测试此接口」会打到未实现的分支
['healthz', 'status', 'models', 'chat'].forEach(ep => {
  ok(code.includes('data-test="' + ep + '"') || code.includes("data-test=\"'+test"),
    '接口示例有测试按钮：' + ep);
});

// ---------- 4c. 成长计划：两个按钮的接线 ----------
// 目标：一键接取 / 一键领奖，其余入口一律不暴露（补跑会写上游上报，已从面板撤下）
ok(code.includes('data-act="tasks_all"'), '动作区使用聚合动作 tasks_all');
['accept', 'claim'].forEach(seg =>
  ok(code.includes('data-only="' + seg + '"'), '存在按钮 data-only="' + seg + '"'));
ok(code.includes('一键接取所有任务') && code.includes('一键领取所有积分奖励'),
  '两个按钮的文案就位');
ok(!/data-only="run"/.test(code), '面板不再暴露补跑（run）入口');
ok(!/data-only=""/.test(code), '不再有「三段全跑」入口（只留两个按钮）');
ok(!/taskSel/.test(code), '已移除单任务下拉 #taskSel（不再是「一个个手动点」）');
ok(!/taskYes/.test(code), '「真实执行」开关已替换为「仅预览」开关');
ok(code.includes('id="taskDry"'), '存在「仅预览」开关 #taskDry');
ok(code.includes('var TASK_SEG='), 'TASK_SEG 分段命名表存在');
ok(code.includes('/app/tasks_all.py'), '命令展示指向聚合脚本 tasks_all.py');
ok(!/\/app\/scripts\/task_/.test(code), '命令展示不再指向单个 task_*.py');
ok(html.includes('id="taskCmd"') && html.includes('id="taskNote"'),
  '任务命令与状态提示容器存在');

// ---------- 4d. 一键导入 cc-switch（官方深链协议） ----------
// 走 ccswitch:// 深链，不碰 cc-switch 的 SQLite 库
ok(html.includes('id="ccswitch"'), '存在 #ccswitch 容器');
ok(code.includes('ccswitch://v1/import?'), '生成 cc-switch 官方深链（ccswitch://v1/import）');
ok(code.includes('function ccSwitchLink') && code.includes('function renderCcSwitch')
   && code.includes('function openDeepLink'), '深链生成 / 渲染 / 唤起三个函数就位');
ok(code.includes('data-act="ccswitch"'), '存在导入按钮 data-act="ccswitch"');
ok(code.includes('"apiFormat","openai_chat"'), '深链附带 apiFormat=openai_chat');
['resource', 'app', 'name', 'endpoint', 'apiKey', 'model'].forEach(k =>
  ok(new RegExp('\\["' + k + '"').test(code), '深链含参数 ' + k));
ok(code.includes('HKEY_CLASSES_ROOT'), '注明了协议注册位置');
// cc-switch 的注册表键是 ccswitch（无连字符），别写成 cc-switch
ok(!/HKEY_CLASSES_ROOT\\\\cc-switch/.test(code), '注册表键名写的是 ccswitch 而不是 cc-switch');
ok(code.includes('15721'), '说明了 cc-switch 本地代理端口（15721）');

// ---------- 4e. 按钮「执行中」状态与反馈 ----------
// 用户反馈：点「一键接取所有任务」一点反应都没有。根因两条：
//   ① 作业输出区在面板最底部，输出落在视野之外；
//   ② 按钮本身无任何变化，也没有常驻的成功/失败提示。
ok(/\.spin\{/.test(html) && /@keyframes spin/.test(html), '有转圈动画样式 .spin');
ok(/\.btn:disabled/.test(html), '有按钮禁用态样式 .btn:disabled');
ok(/\.actstatus\.run/.test(html) && /\.actstatus\.ok/.test(html) && /\.actstatus\.bad/.test(html),
  '状态条有 run / ok / bad 三种样式');
ok(html.includes('id="actStatus"'), '存在 #actStatus 常驻状态条容器');
ok(html.indexOf('id="jobArea"') < html.indexOf('id="acts"'),
  '作业输出区 #jobArea 位于按钮区 #acts 之前（放底部会让输出落在视野外，等于没反馈）');
['applyPending', 'markPending', 'clearPending', 'setActStatus', 'startStatusTicker', 'revealJobArea']
  .forEach(fn => ok(code.includes('function ' + fn + '('), '存在 ' + fn + '()'));
ok(code.includes('b.disabled=true') && code.includes('b.disabled=false'),
  '按钮会被真正禁用 / 恢复');
ok(code.includes('class="spin"'), '按钮忙碌态插入转圈元素');
ok(code.includes('markPending(key,label)') && code.includes('clearPending(key)'),
  'runAction 标记 pending、pollJob 结束时清除（配对）');
// 用 lastIndexOf：applyPending(); 在 markPending/clearPending 里也出现，取最后一次才是 renderMeta 里那次
ok(code.lastIndexOf('applyPending();') > code.indexOf('renderTutorial();'),
  'renderMeta 末尾重新套用 pending（否则一次刷新就把忙碌态冲掉）');
ok(code.includes('scrollIntoView'), '启动作业后把输出区滚动进视野');

// ---------- 4f. 双模式（native / docker）----------
// 面板必须同时保留两种模式的分支代码：native（bin\wb2api.exe 直启）与 docker（容器）
ok(code.includes('m.mode==="native"'), '前端按 meta.mode 分支渲染（native/docker）');
ok(code.includes('bin\\\\signin_bin.exe'), 'native 分支：签到命令指向 bin\\signin_bin.exe');
ok(code.includes('bin\\\\credit.exe'), 'native 分支：日报命令指向 bin\\credit.exe');
ok(code.includes('docker exec '), 'docker 分支：容器命令仍保留（双模式兼容）');
ok(code.includes('重启网关') && code.includes('重启容器'),
  '重载账号按钮两种文案都在（按模式显示其一）');
ok(code.includes('gateway.log 增量跟随'), 'native：实时日志标注为文件跟随');

// ---------- 4g. 本机客户端账号（读取客户端明文登录态 → 抓取入池）----------
ok(code.includes('function renderLocalAccounts'), '存在本机账号渲染函数 renderLocalAccounts');
ok(html.includes('id="localAccounts"') && html.includes('id="localTag"'),
  '账号池页签存在本机客户端区块容器');
ok(/renderTabs\(\);\s*renderActions\(\);\s*renderLocalAccounts\(\)/.test(code),
  'renderMeta 已挂载本机账号渲染（meta 刷新时同步）');
ok(code.includes('S.meta.local_accounts') && code.includes('S.meta.desktop_logins'),
  '前端合并两个来源：本地身份 + 客户端登录态');
ok(code.includes('function importLocalLogins') && code.includes('data-import'),
  '存在抓取入池动作（importLocalLogins + data-import 按钮）');
ok(code.includes('/api/import-local-login'), '抓取走同源 /api/import-local-login');
ok(code.includes('data-login-open') && !code.includes('data-act="login_url"'),
  '登录入口统一为弹窗（运维区旧的两步按钮已合并）');
ok(code.includes('data-ltab') && html.includes('id="ltab-token"') && html.includes('id="ltab-local"'),
  '弹窗三标签页：OAuth 授权 / Token / 本地导入');
ok(code.includes('/api/login/token') && code.includes('data-token-add'),
  'Token 粘贴添加已接线（/api/login/token）');
ok(!code.includes('decrypt') && !code.includes('$wbEncrypted'),
  '前端不含解密逻辑（客户端登录态为明文，直接读取）');
ok(code.includes('function loginModalOpen') && code.includes('function autoReloadGateway'),
  '存在授权弹窗与自动重载逻辑（loginModalOpen / autoReloadGateway）');
ok(html.includes('id="loginMask"') && html.includes('id="loginUrl"') && html.includes('id="loginWait"'),
  '授权弹窗容器齐全（遮罩 / 地址框 / 等待条）');
ok(code.includes('/api/login/begin') && code.includes('/api/login/check'),
  '授权弹窗走同源 begin/check 接口（轮询由前端驱动，关窗即停）');
ok(/importLocalLogins[\s\S]*?autoReloadGateway\(\)/.test(code),
  '抓取入池成功后自动重载网关（无需手动去运维区点）');
ok(code.includes('loginPollStop') && /loginModalClose\(\)\{[\s\S]*?loginPollStop\(\)/.test(code),
  '关闭弹窗立即停止轮询');
ok(html.includes('data-open-auth'), '「在浏览器中打开」按钮走事件委托（不受 DOM 顺序影响）');
ok(code.indexOf('tgt.id==="loginMask"') >= 0, '点遮罩空白处关闭弹窗');
var _mo = code.match(/function loginModalOpen\(\)\{[\s\S]*?\n\}/);
ok(_mo && !/window\.open/.test(_mo[0]), '打开弹窗时不自动跳转浏览器（等用户点按钮）');

// ---------- 4h. DOM 顺序安全 ----------
// 页面脚本在 <script> 处同步执行，此时其后方的 DOM 还不存在；
// 凡是在启动期（bind() 内）直接 $("...") 访问的元素，必须位于 <script> 之前。
// —— 弹窗 DOM 就踩过这个坑：bind() 里绑 #loginOpen 恒为 null，点了没反应。
(function () {
  const scriptAt = html.indexOf('<script>');
  const head = html.slice(0, scriptAt);
  const bindBody = (code.match(/function bind\(\)\{[\s\S]*?\n\}/) || [''])[0];
  const ids = [];
  // 只取「启动期直接绑定事件」的写法：$("#x").onclick=… —— 动态渲染区里的
  // var el=$("#x"); if(el)el.onchange=… 不算（那些元素本来就由 render* 生成）
  const re = /\$\("#([A-Za-z0-9_\-]+)"\)\.(?:onclick|onchange|oninput|onkeydown|onsubmit)/g;
  let m;
  while ((m = re.exec(bindBody))) ids.push(m[1]);
  ok(ids.length > 0, 'bind() 内确有直接事件绑定（共 ' + ids.length + ' 个）');
  ids.forEach(function (id) {
    ok(head.indexOf('id="' + id + '"') >= 0,
      'bind() 访问的 #' + id + ' 位于脚本之前（DOM 顺序安全）');
  });
})();

// ---------- 4i. 错误提示渲染：对象型错误不得变成 [object Object] ----------
// 网关的错误体是 {"error":{"code":..,"message":..}} 对象。曾经 showAlert 直接
// esc(o.error)，于是每个页签顶部都挂着一条 [object Object]，且看不到真实原因。
ok(code.includes('function errText'), '存在错误信息归一化函数 errText');
ok(/typeof v\s*===\s*"object"/.test(code), 'errText 会处理对象型错误值');
ok(!/esc\(o\.error\s*\|\|/.test(code),
  'showAlert 不再直接 esc(o.error)（否则对象型错误会渲染成 [object Object]）');
ok(code.includes('__wb2apiTest'), '暴露了测试钩子，供运行时测试驱动内部渲染函数');
ok(code.includes('invalid_api_key'), '401（api_key 不一致）有专门的可读提示');

// ---------- 4j. 本机账号拉黑 / 账号池移除 ----------
ok(code.includes('function blockLocalLogin') && code.includes('data-block='),
  '本机账号有「拉黑」入口（blockLocalLogin + data-block）');
ok(code.includes('/api/block-local-login'), '拉黑走同源 /api/block-local-login');
ok(code.includes('function unblockLocalLogin') && code.includes('data-unblock='),
  '拉黑名单可查看并手动解除（unblockLocalLogin + data-unblock）');
ok(code.includes('data-block-list') && code.includes('showBlocked'),
  '存在「已拉黑」名单开关（S.showBlocked）');
ok(/rows\s*=\s*rows\.filter\(function\(a\)\{return !blockedUids\[a\.uid\]\}\)/.test(code),
  '被拉黑的账号在前端会被过滤掉（两个数据源都过滤）');
ok(code.includes('dl.unblocked'), '重新登录后自动解除的结果会被前端提示一次');
ok(/blockLocalLogin[\s\S]{0,600}?confirm\(/.test(code), '拉黑有二次确认');
ok(code.includes('function removePoolAccount') && code.includes('data-pool-remove='),
  '账号池每行有「移除」按钮（removePoolAccount + data-pool-remove）');
ok(code.includes('/api/pool/remove'), '移除走同源 /api/pool/remove');
ok(/removePoolAccount[\s\S]{0,900}?confirm\(/.test(code), '移除有二次确认');
ok(/removePoolAccount[\s\S]{0,1600}?autoReloadGateway\(\)/.test(code),
  '移除账号后自动重载网关（立即停止参与选号）');
ok(code.includes('removed-auths'), '移除时提示了备份位置（removed-auths/）');
ok(/cols\.map[\s\S]{0,220}?操作<\/th>/.test(code), '账号池表头追加了「操作」列');
ok(code.includes('colspan="13"'), '账号池空态 colspan 已同步为 13 列');

// ---------- 5. 兜底：不应出现未转义的 </script> 或裸 fetch 跨域 ----------
ok(!/fetch\(\s*["'`]http:\/\/localhost:7863/.test(code),
   '前端不直接跨域请求 7863（全部走同源 /api/*）');
ok(code.includes('/api/status') && code.includes('/api/run') && code.includes('/api/job'),
   '关键接口引用齐全');

// ---------- 6. 逻辑单元测试：状态判定 / 权重 / 时长 ----------
// 从页面里抠出纯函数来跑（避免整页 DOM 依赖）
const sandbox = { console, Date, Math, isNaN, parseInt, parseFloat, String, Number, JSON };
vm.createContext(sandbox);
function grab(name) {
  const re = new RegExp('function ' + name + '\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n\\}');
  const g = code.match(re);
  if (!g) throw new Error('找不到函数 ' + name);
  return g[0];
}
const helpers = ['esc', 'num', 'zeroT', 'dur'].map(grab).join('\n');
vm.runInContext(helpers + '\n' + grab('stateOf') + '\n' + grab('successRate') +
  '\n' + grab('visibleWeight') + '\n' + grab('isEligible'), sandbox);

const cfg = { pool: { max_in_flight: 3, idle_weight_per_hour: 0.5, idle_weight_max: 5 } };
const Z = '0001-01-01T00:00:00Z';
const future = new Date(Date.now() + 600000).toISOString();

const t = (name, fn) => { try { fn(); } catch (e) { ok(false, name + ' → ' + e.message); return; } };
function eq(a, b, msg) { ok(a === b, msg + ' (得到 ' + a + ')'); }

t('状态判定', () => {
  eq(sandbox.stateOf({ uid: 'u1', disabled: false, cooling: false, until: Z, breaker_until: Z, in_flight: 0 }, cfg).k,
     'healthy', '健康账号判定');
  eq(sandbox.stateOf({ uid: 'u2', disabled: true, disabled_reason: 'x', until: Z, breaker_until: Z }, cfg).k,
     'disabled', '禁用账号判定');
  eq(sandbox.stateOf({ uid: 'u3', disabled: false, until: future, breaker_until: Z, cool_kind: 'soft_rate', reason: '429 rate limit' }, cfg).k,
     'soft', '软冷却判定');
  eq(sandbox.stateOf({ uid: 'u4', disabled: false, until: future, breaker_until: Z, cool_kind: 'hard_credit', reason: '余额不足' }, cfg).k,
     'hard', '余额硬冷却判定');
  // 关键分支：只有熔断、until 为零值 —— 必须判成 breaker 且剩余时间来自 breaker_until
  const b = sandbox.stateOf({ uid: 'u5', disabled: false, until: Z, breaker_until: future, breaker_fails: 0 }, cfg);
  eq(b.k, 'breaker', '仅熔断判定（until 为零值）');
  ok(b.left > 550 && b.left <= 600, '熔断剩余时间由 breaker_until 计算（得到 ' + Math.round(b.left) + 's）');
  // 在途满：healthy 但在途达上限
  eq(sandbox.stateOf({ uid: 'u6', disabled: false, until: Z, breaker_until: Z, in_flight: 3 }, cfg).k,
     'full', '在途满判定');
});

t('权重公式', () => {
  const pool = [
    { uid: 'a', credits: 100, success_count: 9, err_total: 1 },
    { uid: 'b', credits: 0, success_count: 0, err_total: 0 },
  ];
  const wa = sandbox.visibleWeight(pool[0], 100);   // 1 + 10 + 2.7 = 13.7
  const wb = sandbox.visibleWeight(pool[1], 100);   // 1 + 0 + 1.5 = 2.5
  ok(Math.abs(wa - 13.7) < 1e-9, '高积分高成功率权重 = ' + wa.toFixed(2) + '（期望 13.70）');
  ok(Math.abs(wb - 2.5) < 1e-9, '无记录账号用中性 1.5 = ' + wb.toFixed(2) + '（期望 2.50）');
  eq(sandbox.successRate(pool[1]).r, null, '无请求记录 → 成功率 null');
});

t('时长格式化', () => {
  eq(sandbox.dur(45), '45秒', 'dur(45)');
  eq(sandbox.dur(780), '13分0秒', 'dur(780)');
  eq(sandbox.dur(3661), '1小时1分', 'dur(3661)');
  eq(sandbox.dur(90000), '1天1小时', 'dur(90000)');
});

t('零值时间不误显示', () => {
  eq(sandbox.zeroT(Z), true, '0001-01-01 识别为未设置');
  eq(sandbox.zeroT('2026-09-12T09:07:04+08:00'), false, '正常时间不误判');
});

t('HTML 转义', () => {
  eq(sandbox.esc('<img src=x onerror=1>'), '&lt;img src=x onerror=1&gt;', 'esc 转义尖括号');
  eq(sandbox.esc('a"b\'c&d'), 'a&quot;b&#39;c&amp;d', 'esc 转义引号与 &');
});

console.log('\n' + (fail === 0 ? '=== 全部通过 ===' : '=== ' + fail + ' 项失败 ==='));
process.exit(fail === 0 ? 0 : 1);
