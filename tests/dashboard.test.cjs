const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../dashboard/index.html'), 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const template = fs.readFileSync(path.join(__dirname, '../templates/task.md'), 'utf8');

function fixture() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      innerHTML: '', textContent: '', value: '', dataset: {}, style: {},
      classList: {add(){}, remove(){}, toggle(){}, contains(){return false;}},
      addEventListener(){}, setAttribute(){}, querySelectorAll(){return [];}, focus(){},
    });
    return elements.get(id);
  };
  for (const id of ['drawer','dBody','dHead','scrim','toast','tabs','view','kpis','alerts',
                    'authBtn','newLink','fProject','fType','fSystem','modal','q']) element(id);
  const context = vm.createContext({
    console, TextEncoder, TextDecoder,
    setTimeout(){return 1;}, clearTimeout(){},
    fetch(){return new Promise(() => {});},
    location: {hash:'', pathname:'/', search:''}, history:{replaceState(){}},
    localStorage:{getItem(){return null;}, setItem(){}, removeItem(){}},
    document:{querySelector:s => elements.get(s.slice(1)) || null,
      addEventListener(){}, body:element('body')},
    window:{scrollY:0, scrollTo(){}, addEventListener(){}},
    template,
  });
  for (const script of scripts) vm.runInContext(script, context);
  const run = code => vm.runInContext(code, context);
  run(`S.data = {tasks:[], projects:{}, config:{hot_days:3,soon_days:7,next_days:30},
    labels:{status:{inbox:'Входящие',backlog:'Бэклог',todo:'Запланирована',
      'in-progress':'В работе','in-development':'В разработке',done:'Готово',canceled:'Отменено'},
      priority:{now:['Сейчас',''],development:['В разработке',''],next:['Далее',''],later:['Потом','']},
      moscow:{must:['Must',''],should:['Should','']},type:{analysis:'Аналитика'}}, repo:'test/test'};
    openTask = id => { S.lastOpened = id; };
    GH.putFile = async (path, text) => { S.written = text; return 'saved-sha'; };`);
  return {run, element, elements, context};
}

test('development remains distinct for all deadlines and after parsing saved text', () => {
  const {run} = fixture();
  for (const deadline of ['', '2000-01-01', '2099-01-01']) {
    assert.equal(run(`syncHorizon({status:'in-development', deadline:${JSON.stringify(deadline)}}).status`), 'in-development');
    assert.equal(run(`horizonOf({status:'in-development', deadline:${JSON.stringify(deadline)}})`), 'development');
  }
  assert.equal(run(String.raw`taskFromText('---\nid: BL-900\nstatus: in-development\ndeadline: 2000-01-01\n---\n## Описание\nТекст', 'tasks/test.md').priority`), 'development');
  assert.equal(run(`isPlanned({status:'in-development'})`), true);
});

test('planned columns are independent of overdue, near, distant, or missing deadlines', () => {
  const {run} = fixture();
  for (const priority of ['next','later']) {
    for (const deadline of ['', '2000-01-01', '2099-01-01']) {
      const actual = JSON.parse(run(`JSON.stringify(syncHorizon({status:'todo',priority:${JSON.stringify(priority)},deadline:${JSON.stringify(deadline)}}))`));
      assert.equal(actual.status, 'todo');
      assert.equal(actual.priority, priority);
    }
  }
  assert.equal(run(`syncHorizon({status:'waiting',priority:'later',deadline:'2000-01-01'}).status`), 'todo');
  assert.equal(run(`syncHorizon({status:'waiting',priority:'later',deadline:'2000-01-01'}).priority`), 'later');
  assert.equal(run(`'waiting' in S.data.labels.status`), false);
});

test('handoff persists status and journal, preserves legacy sections, and can return to personal work', async () => {
  const {run} = fixture();
  run(String.raw`S.edit = {id:'BL-900',path:'tasks/test.md',sha:'old',text:'---\nid: BL-900\ntitle: Тест\nstatus: in-progress\npriority: now\ndeadline: 2000-01-01\n---\n## Суть\nИсходное описание\n\n## Что сделать\n- [x] Уже сделано\n\n## Журнал\n- 2026-01-01 — старая запись\n'};`);
  await run(`setStatus('in-development')`);
  assert.equal(run(`taskFromText(S.written, 'tasks/test.md').status`), 'in-development');
  assert.match(run('S.written'), /priority: development/);
  assert.match(run('S.written'), /статус: В работе → В разработке/);
  assert.match(run('S.written'), /- \[x\] Уже сделано/);
  assert.match(run('S.written'), /старая запись/);
  await run(`setStatus('in-progress')`);
  assert.equal(run(`taskFromText(S.written, 'tasks/test.md').priority`), 'now');
});

test('handoff retries on conflict while preserving concurrent description changes', async () => {
  const {run} = fixture();
  run(String.raw`S.edit = {id:'BL-900',path:'tasks/test.md',sha:'old',text:'---\nid: BL-900\ntitle: Тест\nstatus: todo\n---\n## Описание\nСтарый текст\n'};
    let attempts = 0;
    GH.putFile = async (path,text) => { if(!attempts++){ const e=new Error('conflict'); e.status=409; throw e; } S.written=text; return 'saved'; };
    GH.getFile = async () => ({sha:'fresh',text:S.edit.text.replace('Старый текст','Правка другого пользователя')});`);
  await run(`setStatus('in-development')`);
  assert.match(run('S.written'), /Правка другого пользователя/);
  assert.match(run('S.written'), /status: in-development/);
});

test('return to plan keeps chosen column and records column, deadline, and comment', async () => {
  const {run} = fixture();
  run(String.raw`S.edit = {id:'BL-900',path:'tasks/test.md',sha:'old',text:'---\nid: BL-900\ntitle: Тест\nstatus: in-development\npriority: development\ndeadline: 2000-01-01\n---\n## Описание\nИсходные данные\n'};`);
  await run(`setStatus('todo', 'Вернемся позже', {priority:'later',deadline:'2001-01-01'})`);
  assert.equal(run(`taskFromText(S.written, 'tasks/test.md').status`), 'todo');
  assert.equal(run(`taskFromText(S.written, 'tasks/test.md').priority`), 'later');
  assert.match(run('S.written'), /колонка: В разработке → Потом/);
  assert.match(run('S.written'), /срок: 01.01.2000 → 01.01.2001/);
  assert.match(run('S.written'), /Вернемся позже/);
  await run(`setStatus('todo', '', {priority:'next',deadline:''})`);
  assert.equal(run(`taskFromText(S.written, 'tasks/test.md').priority`), 'next');
  await run(`setStatus('todo', '', {deadline:'2099-01-01'})`);
  assert.equal(run(`taskFromText(S.written, 'tasks/test.md').priority`), 'next');
});

test('editing only a deadline preserves the manually selected planned column', async () => {
  const {run, element} = fixture();
  run(String.raw`S.edit = {id:'BL-900',path:'tasks/test.md',sha:'old',text:'---\nid: BL-900\ntitle: Тест\nstatus: todo\npriority: later\ndeadline: 2099-01-01\n---\n## Описание\nИсходное описание\n'};`);
  const values = {f_title:'Тест',f_status:'todo',f_priority:'later',f_deadline:'2000-01-01',
    f_moscow:'',f_project:'',f_impact:'',f_type:'',f_system:'',f_initiator:'',f_bitrix:'',f_jnew:''};
  for(const [id,value] of Object.entries(values)) element(id).value=value;
  await run('saveEdit()');
  assert.equal(run(`taskFromText(S.written, 'tasks/test.md').priority`), 'later');
  assert.equal(run(`taskFromText(S.written, 'tasks/test.md').status`), 'todo');
  assert.match(run('S.written'), /Исходное описание/);
});

test('horizon renders four columns and no navigation points to the removed tab', () => {
  const {run, element} = fixture();
  run(`S.data.tasks = [{id:'BL-900',title:'Передано',status:'in-development',priority:'development',tags:[]}]; render();`);
  assert.equal((element('view').innerHTML.match(/class="col"/g)||[]).length, 4);
  assert.equal((element('view').innerHTML.match(/data-id="BL-900"/g)||[]).length, 1);
  assert.doesNotMatch(element('tabs').innerHTML + element('kpis').innerHTML, /data-tab="moscow"/);
  assert.equal(run(`TAB_MIGRATE.moscow`), 'prio');
});

test('editing an archived task preserves its last column and closing date', async () => {
  const {run, element} = fixture();
  run(String.raw`S.edit = {id:'BL-900',path:'archive/2026/test.md',sha:'old',text:'---\nid: BL-900\ntitle: Тест\nstatus: done\npriority: development\nclosed: 2026-11-01\n---\n## Описание\nИсходное описание\n'};`);
  const values = {f_title:'Тест после правки',f_status:'done',f_priority:'next',f_deadline:'',
    f_moscow:'',f_project:'',f_impact:'',f_type:'',f_system:'',f_initiator:'',f_bitrix:'',f_jnew:''};
  for(const [id,value] of Object.entries(values)) element(id).value=value;
  await run('saveEdit()');
  assert.equal(run(`taskFromText(S.written, 'archive/2026/test.md').priority`), 'development');
  assert.equal(run(`taskFromText(S.written, 'archive/2026/test.md').closed`), '2026-11-01');
});

test('new task has one description and saves it without removed fields or legacy sections', async () => {
  const {run, element} = fixture();
  run(`formShell = (title,sub,inner) => { S.formHtml = inner; }; openCreate();`);
  assert.match(run('S.formHtml'), /id="n_description"/);
  for (const id of ['f_estimate','f_tags','n_sut','n_ctx','n_next','n_dep','n_todo','n_done']) {
    assert.ok(!run('S.formHtml').includes(`id="${id}"`), id);
  }
  const values = {f_title:'Новая проверка',f_status:'todo',f_priority:'later',f_deadline:'2000-01-01',
    f_moscow:'must',f_project:'',f_impact:'Полезна',f_type:'analysis',f_system:'1С',
    f_initiator:'',f_bitrix:'',n_description:'Суть и контекст вместе\nВторая строка',n_mat:'https://example.test'};
  for (const [id,value] of Object.entries(values)) element(id).value = value;
  run(`GH.tree = async () => ['tasks/BL-900-test.md']; GH.getFile = async () => ({text:template});`);
  await run('saveNew()');
  const text = run('S.written');
  assert.match(text, /status: todo/);
  assert.match(text, /priority: later/);
  assert.match(text, /## Описание\nСуть и контекст вместе\nВторая строка/);
  assert.match(text, /## Материалы/);
  assert.match(text, /## Журнал/);
  for (const removed of ['estimate:', 'tags:', '## Суть', '## Контекст', '## Следующий шаг',
                         '## Зависит от','## Что сделать','## Критерии готовности']) {
    assert.ok(!text.includes(removed), removed);
  }
});

