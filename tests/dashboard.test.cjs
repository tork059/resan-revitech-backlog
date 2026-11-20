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
      'in-progress':'В работе','ready-for-development':'Готово к реализации','in-development':'В разработке','in-testing':'На тестировании',done:'Готово',canceled:'Отменено'},
      priority:{now:['В работе','делаю сейчас'],ready:['Готово к реализации','можно передавать разработчикам'],development:['В разработке','передано разработчикам'],testing:['На тестировании','проверка результата'],next:['Запланировано','Выполнение в рамках месяца'],later:['Отложено','Запланировано в рамках более месяца']},
      moscow:{must:['Must',''],should:['Should','']},type:{analysis:'Аналитика'}}, repo:'test/test'};
    openTask = id => { S.lastOpened = id; };
    GH.putFile = async (path, text) => { S.written = text; return 'saved-sha'; };`);
  return {run, element, elements, context};
}

test('planning preserves each working stage, starts in work, and returns to backlog', async () => {
  for(const status of ['backlog','inbox','in-progress','ready-for-development','in-development','in-testing']){
    const {run}=fixture();
    run(`S.edit={id:'BL-900',path:'tasks/test.md',sha:'old',text:${JSON.stringify('---\nid: BL-900\ntitle: Тест\nstatus: '+status+'\ndeadline: 2000-01-01\n---\n## Описание\nИсходный текст\n\n## Журнал\n- 2026-01-01 — старая запись\n')}};
      S.data.tasks=[taskFromText(S.edit.text,S.edit.path)];`);
    await run('sendToNextSprint()');
    assert.equal(run(`taskFromText(S.written,'tasks/test.md').sprint`),'next');
    assert.equal(run(`taskFromText(S.written,'tasks/test.md').status`),['backlog','inbox'].includes(status)?'todo':status);
    assert.match(run('S.written'),/Исходный текст/);
    assert.match(run('S.written'),/старая запись/);
    assert.match(run('S.written'),/планирование:/);
    const first=run('S.written');
    await run('sendToNextSprint()');
    assert.equal(run('S.written'),first);
    await run(`setStatus('in-progress','',{sprint:'current'})`);
    assert.equal(run(`taskFromText(S.written,'tasks/test.md').sprint`),'current');
    assert.equal(run(`taskFromText(S.written,'tasks/test.md').status`),'in-progress');
    assert.equal(run(`taskFromText(S.written,'tasks/test.md').priority`),'now');
    await run('sendToNextSprint()');
    await run(`setStatus('backlog')`);
    assert.equal(run(`taskFromText(S.written,'tasks/test.md').sprint`),'');
    assert.equal(run(`taskFromText(S.written,'tasks/test.md').status`),'backlog');
  }
});

test('planning order persists, ignores deadlines, and filtered movement preserves hidden tasks', async () => {
  const {run,element}=fixture();
  run(`GH.token='test-only'; S.texts={};
    for(const [id,title,deadline] of [['BL-900','Видимая А','2099-01-01'],['BL-901','Скрытая','2000-01-01'],['BL-902','Видимая Б','']]){
      S.texts[id]='---\\nid: '+id+'\\ntitle: '+title+'\\nstatus: todo\\nsprint: next\\ndeadline: '+deadline+'\\n---\\n## Описание\\nСохранить описание\\n';
      S.data.tasks.push(taskFromText(S.texts[id],'tasks/'+id+'.md'));
    }
    GH.getFile=async path=>({path,sha:'fresh',text:S.texts[path.split('/').pop().replace('.md','')]});
    GH.putFile=async(path,text)=>{const id=path.split('/').pop().replace('.md',''); S.texts[id]=text; S.written=text; return 'saved';};
    S.q='Видимая'; S.tab='planning'; render();`);
  await run(`movePlanning('BL-902','up')`);
  assert.deepEqual(JSON.parse(run(`JSON.stringify(S.data.tasks.slice().sort(byPlanning).map(t=>t.id))`)),['BL-902','BL-900','BL-901']);
  run(`S.data.tasks=S.data.tasks.map(t=>taskFromText(S.texts[t.id],t.path)); S.q=''; render();`);
  assert.ok(element('view').innerHTML.indexOf('data-id="BL-902"')<element('view').innerHTML.indexOf('data-id="BL-900"'));
  assert.match(run('S.written'),/Сохранить описание/);
  assert.match(run('S.written'),/выше в очереди/);
  await run(`planningAction('BL-902','current')`);
  assert.equal(run(`S.data.tasks.find(t=>t.id==='BL-902').priority`),'now');
  assert.equal(run(`S.data.tasks.find(t=>t.id==='BL-902').sprint`),'current');
  await run(`planningAction('BL-900','backlog')`);
  assert.equal(run(`S.data.tasks.find(t=>t.id==='BL-900').status`),'backlog');
  assert.equal(run(`S.data.tasks.filter(isNextSprint).length`),1);
});

test('planning stage survives description editing, closing, and conflict retries', async () => {
  const {run,element}=fixture();
  run(String.raw`S.edit={id:'BL-900',path:'tasks/test.md',sha:'old',text:'---\nid: BL-900\ntitle: Тест\nstatus: in-testing\npriority: testing\nsprint: next\nplanning_order: 1234\n---\n## Описание\nИсходное описание\n'};`);
  for(const [id,value] of Object.entries({f_title:'Тест',f_status:'in-testing',f_deadline:'2000-01-01',f_jnew:'Комментарий'})) element(id).value=value;
  await run('saveEdit()');
  assert.equal(run(`taskFromText(S.written,'tasks/test.md').sprint`),'next');
  assert.equal(run(`taskFromText(S.written,'tasks/test.md').planning_order`),'1234');
  await run(`setStatus('done')`);
  assert.equal(run(`isNextSprint(taskFromText(S.written,'tasks/test.md'))`),false);
  assert.equal(run(`taskFromText(S.written,'tasks/test.md').sprint`),'next');
  await run(`setStatus('in-progress','',{sprint:'current'})`);
  run(`let planningAttempts=0; GH.putFile=async(path,text)=>{if(!planningAttempts++){const e=new Error('conflict');e.status=409;throw e;} S.written=text;return 'saved';};
    GH.getFile=async()=>({sha:'fresh',text:S.edit.text.replace('Исходное описание','Одновременная правка')});`);
  await run('sendToNextSprint()');
  assert.match(run('S.written'),/Одновременная правка/);
  assert.equal(run(`taskFromText(S.written,'tasks/test.md').sprint`),'next');
});

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
  assert.match(run('S.written'), /колонка: В разработке → Отложено/);
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

test('sprint keeps four workflow stages and excludes planning tasks from board and counters', () => {
  const {run, element} = fixture();
  run(`S.data.tasks = [
    {id:'BL-900',title:'Передано',status:'in-development',priority:'development',tags:[]},
    {id:'BL-901',title:'Готово к реализации',status:'ready-for-development',priority:'ready',tags:[]},
    {id:'BL-902',title:'Проверка',status:'in-testing',priority:'testing',tags:[]},
    {id:'BL-903',title:'На следующей неделе',status:'in-testing',priority:'testing',sprint:'next',tags:[]},
    {id:'BL-904',title:'Старый план',status:'todo',priority:'later',tags:[]}]; render();`);
  assert.deepEqual([...element('view').innerHTML.matchAll(/data-column="([^"]+)"/g)].map(m=>m[1]),
    ['now','ready','development','testing']);
  for(const id of ['BL-900','BL-901','BL-902'])
    assert.equal((element('view').innerHTML.match(new RegExp(`data-id="${id}"`,'g'))||[]).length, 1);
  assert.doesNotMatch(element('tabs').innerHTML + element('kpis').innerHTML, /data-tab="moscow"/);
  assert.match(element('tabs').innerHTML, /Планирование<span class="n">2/);
  assert.match(element('tabs').innerHTML, /Спринт<span class="n">3/);
  assert.doesNotMatch(element('view').innerHTML, /data-id="BL-90[34]"/);
  run(`S.tab='planning'; render();`);
  assert.match(element('view').innerHTML, /На следующий спринт/);
  assert.match(element('view').innerHTML, /data-id="BL-903"/);
  assert.match(element('view').innerHTML, /data-id="BL-904"/);
  assert.equal(run(`TAB_MIGRATE.moscow`), 'prio');
});

test('new stages persist through saving, parsing, deadline changes, completion and reopening', async () => {
  const {run} = fixture();
  run(String.raw`S.edit = {id:'BL-900',path:'tasks/test.md',sha:'old',text:'---\nid: BL-900\ntitle: Тест\nstatus: in-progress\npriority: now\ndeadline: 2000-01-01\n---\n## Описание\nСохранить данные\n\n## Журнал\n- 2026-01-01 — исходная запись\n'};`);
  for(const [status,column] of [['ready-for-development','ready'],['in-development','development'],['in-testing','testing']]) {
    await run(`setStatus(${JSON.stringify(status)})`);
    assert.equal(run(`taskFromText(S.written, 'tasks/test.md').status`), status);
    assert.equal(run(`taskFromText(S.written, 'tasks/test.md').priority`), column);
    assert.equal(run(`isClosed(taskFromText(S.written, 'tasks/test.md'))`), false);
    assert.equal(run(`isPlanned(taskFromText(S.written, 'tasks/test.md'))`), true);
    for(const deadline of ['', '2000-01-01','2099-01-01']) {
      assert.equal(run(`syncHorizon({status:${JSON.stringify(status)},deadline:${JSON.stringify(deadline)}}).priority`), column);
    }
  }
  assert.match(run('S.written'), /статус: В работе → Готово к реализации/);
  assert.match(run('S.written'), /статус: В разработке → На тестировании/);
  assert.match(run('S.written'), /исходная запись/);
  assert.match(run('S.written'), /Сохранить данные/);
  await run(`setStatus('done')`);
  assert.equal(run(`taskFromText(S.written, 'tasks/test.md').priority`), 'testing');
  assert.ok(run(`taskFromText(S.written, 'tasks/test.md').closed`));
  await run(`setStatus('in-testing')`);
  assert.equal(run(`taskFromText(S.written, 'tasks/test.md').closed`), '');
  await run(`setStatus('todo', '', {priority:'later'})`);
  assert.equal(run(`taskFromText(S.written, 'tasks/test.md').priority`), 'later');
});

test('new action buttons and statuses appear in task cards and forms', () => {
  const {run,element} = fixture();
  const act=element('dHead .d-actions');
  act.querySelector=()=>null;
  act.insertAdjacentHTML=(_,html)=>{act.innerHTML+=html;};
  run(`decorate({status:'in-progress'})`);
  assert.match(act.innerHTML, /data-act="ready">📤 Готово к передаче/);
  assert.match(act.innerHTML, /data-act="testing">👀 В тестирование/);
  const form=run(`formFields({status:'ready-for-development',priority:'ready'}, true)`);
  assert.match(form, /value="ready-for-development" selected/);
  assert.match(form, /value="in-testing"/);
  act.innerHTML='';
  run(`decorate({status:'ready-for-development'})`);
  assert.doesNotMatch(act.innerHTML, /data-act="ready"/);
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
  assert.match(text, /sprint: next/);
  assert.match(text, /planning_order:/);
  assert.match(text, /## Описание\nСуть и контекст вместе\nВторая строка/);
  assert.match(text, /## Материалы/);
  assert.match(text, /## Журнал/);
  for (const removed of ['estimate:', 'tags:', '## Суть', '## Контекст', '## Следующий шаг',
                         '## Зависит от','## Что сделать','## Критерии готовности']) {
    assert.ok(!text.includes(removed), removed);
  }
});



