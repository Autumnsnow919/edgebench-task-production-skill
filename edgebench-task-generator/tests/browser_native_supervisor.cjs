/* Browser control-plane fixture; no models or production actions.
 * Run with EDGEBENCH_PLAYWRIGHT_MODULE pointing to an installed playwright.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require(process.env.EDGEBENCH_PLAYWRIGHT_MODULE || 'playwright');

const webRoot = path.resolve(__dirname, '../web');
const sid = 'ses_fixture1234567890123456';
const child = 'producer-fixture-seed';
const employee = {employee_id:child, parent_id:'producer-fixture', role:'seed-worker',
  status:'needs-owner-decision', transport:'opencode-task', job_root:'/fixture/child',
  runtime:{status:'unbound', can_chat:false}, phase:'seed-review'};
const supervisor = {session_id:sid, status:'interrupted', agent:'edgebench-supervisor',
  title:'Fixture supervisor', directory:'/fixture', can_chat:true, outcome:'interrupted'};
const qcReview={producer_id:'producer-fixture',round:2,decision:'repair_required',severity:'major',
  summary:'fixture: repairs closed; native evidence missing',report_integrity:'verified',
  report_path:'/fixture/qc2.json',report_sha256:'fixture-hash',automatic_fire_allowed:false,
  quality_checks:{Q1:'pass',Q3:'incomplete'},resolved_items:['trusted compilation fixed'],
  findings:[{title:'Native ARM receipt missing',status:'incomplete',severity:'major',requirement:'native validation',evidence:'no receipt',impact:'parity unverified'}],
  notice:'QC-2 后仍未通过，保留员工等待负责人。不自动淘汰。'};
const qcProducer={employee_id:'producer-fixture',parent_id:'supervisor',role:'producer',status:'needs-owner-decision',
  transport:'opencode-task',job_root:'/fixture/producer',runtime:{status:'unbound',can_chat:false},qc_owner_review:qcReview};
const dashboard = {supervisor_id:'fixture', benchmark:'REHEARSAL', workspace:{campaign_id:'fixture'},
  supervisor_runtime:supervisor, employees:[qcProducer], workforce:{registered:1,runtime_running:0,unbound:1},
  alerts:[{employee_id:child,status:'needs-owner-decision',report:{summary:'fixture report'}},
    {employee_id:'producer-fixture',status:'needs-owner-decision',owner_review:qcReview}],
  opencode_sessions:{status:'ok',sessions:[supervisor],active_count:0}};
let forms = [{form_id:'frm_fixture',session_id:sid,session_title:supervisor.title,title:'真实等待工具的测试问题',
  can_reply:true,interaction:'agent-question',metadata:{kind:'question',tool:{messageID:'msg_fixture',id:'call_fixture'}},
  fields:[{key:'q0',title:'是否仅确认测试？',type:'string',required:true,options:[
    {value:'接受测试',label:'接受测试',description:'仅确认测试'},
    {value:'取消测试',label:'取消测试',description:'仅取消测试'}],custom:false}]}];
const posts = [];
const errors = [];
const replyNote = '回答已交给真实等待工具；尚不代表工作执行完成。';

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url,'http://fixture');
  const json = value => {res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
  if(req.method === 'POST') {
    let raw=''; for await(const piece of req) raw+=piece;
    const body=JSON.parse(raw); posts.push({path:url.pathname,body});
    if(url.pathname==='/api/opencode-form-reply') {forms=[];return json({status:'answered',note:replyNote});}
    if(url.pathname==='/api/supervisor-review' || url.pathname==='/api/message') return json({status:'admitted',inbox_id:'msg_admitted'});
    res.writeHead(400);return res.end('{}');
  }
  if(url.pathname==='/api/supervisors') return json({supervisors:[{supervisor_id:'fixture',benchmark:'REHEARSAL',workspace:{campaign_id:'fixture'}}]});
  if(url.pathname==='/api/dashboard') return json(url.searchParams.has('employee_id') ? {...dashboard,employees:[url.searchParams.get('employee_id')===child?employee:qcProducer]} : dashboard);
  if(url.pathname==='/api/events') return json({events:[]});
  if(url.pathname==='/api/opencode-forms') return json({status:'ok',forms});
  if(url.pathname==='/api/supervisor-session') return json({status:'ok',session_id:sid,runtime:supervisor,messages:[{type:'assistant',text:'fixture 真实上司回复',created_at:Date.now()}]});
  if(url.pathname==='/api/stream') {
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    return res.end(`event: dashboard\ndata: ${JSON.stringify(dashboard)}\n\n`);
  }
  const member=url.pathname==='/'?'index.html':url.pathname.slice(1);
  if(!['index.html','style.css','app.js','decisions.js'].includes(member)) {res.writeHead(404);return res.end();}
  res.writeHead(200,{'Content-Type':member.endsWith('.js')?'text/javascript':member.endsWith('.css')?'text/css':'text/html'});
  res.end(fs.readFileSync(path.join(webRoot,member)));
});

(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    page.on('dialog',dialog=>dialog.accept());
    await page.goto(`http://127.0.0.1:${server.address().port}/?supervisor_id=fixture`);
    await page.locator('#native-question-list .native-question').waitFor();
    assert.equal(await page.locator('#supervisor-status').textContent(),'已中断');
    assert.equal(await page.getByRole('heading',{name:'上司处理与负责人复核'}).count(),1);
    await page.locator('[data-review="producer-fixture"]').click();
    await page.locator('.qc-owner-review').waitFor();
    const qcText=await page.locator('.qc-owner-review').textContent();
    assert.ok(qcText.includes('trusted compilation fixed'));
    assert.ok(qcText.includes('Q3：证据未完成'));
    assert.ok(qcText.includes('Native ARM receipt missing'));
    assert.ok(qcText.includes('不自动淘汰'));
    await page.locator('#close-detail').click();
    const boardChoice=page.locator('#native-question-list input[type=radio]').first();
    await boardChoice.check();
    await page.locator(`[data-review="${child}"]`).click();
    assert.equal(await page.locator('#inspector').getAttribute('aria-hidden'),'false');
    await page.getByRole('button',{name:'待回答',exact:true}).click();
    await page.locator('#detail-question-list .native-question').waitFor();
    const detailChoices=page.locator('#detail-question-list input[type=radio]');
    await detailChoices.nth(1).check();
    assert.equal(await boardChoice.isChecked(),true,'Inspector radio group must not clear the board selection');
    // The five-second poll used to replace all inspector fields, losing input.
    await page.waitForTimeout(6200);
    assert.equal(await boardChoice.isChecked(),true,'Board selection survives polling');
    assert.equal(await detailChoices.nth(1).isChecked(),true,'Inspector selection survives polling');
    await detailChoices.first().check();
    await page.locator('#detail-question-list').getByRole('button',{name:'下一步：核对回答'}).click();
    await page.locator('#detail-question-list').getByRole('button',{name:'确认提交回答'}).click();
    await page.waitForFunction(()=>document.getElementById('native-answer-result').textContent.includes('真实等待工具'));
    const reply=posts.find(p=>p.path==='/api/opencode-form-reply');
    assert.deepEqual(reply.body.answer,{q0:'接受测试'},'Acceptance has no forced reason');
    assert.equal(reply.body.intent,'production');
    assert.equal(posts.filter(p=>p.path==='/api/owner-decision').length,0);
    await page.locator('#close-detail').click();
    await page.locator(`[data-supervisor-review="${child}"]`).click();
    await page.waitForFunction(()=>!document.getElementById('command-modal').classList.contains('hidden'));
    assert.equal(posts.filter(p=>p.path==='/api/supervisor-review').length,1);
    await page.locator('#command-input').fill('只汇报方案，禁止试跑');
    await page.locator('#command-send').click();
    await page.waitForFunction(()=>document.getElementById('command-result').textContent.includes('真实会话已接收'));
    assert.equal(posts.filter(p=>p.path==='/api/message').length,1);
    assert.equal(posts.filter(p=>p.path==='/api/owner-form-create').length,0);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({status:'pass',mode:'browser-fixture',checks:[
      'interrupted supervisor visible','child report opens','duplicate radios isolated',
      'poll preserves selected answers','acceptance without reason','native reply only',
      'review asks supervisor instead of detached form','message targets actual supervisor',
      'QC2 brief distinguishes incomplete from failed and shows closed repairs'],posts},null,2));
  } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
