/** Built UI checks for durable job recovery and late-submit unmounts. Uses skills-ui-server.mjs. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.HS_PLAYWRIGHT_MODULE||'/Users/pengyanlun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.HS_CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
const page=await browser.newPage(),base=process.env.HS_SKILLS_QA_URL||'http://127.0.0.1:8030';
try{
 await fetch(`${base}/__qa/reset`,{method:'POST'});
 await page.goto(base);await page.getByRole('button',{name:'技能库',exact:true}).click();
 await page.getByRole('searchbox',{name:'搜索技能'}).fill('社媒文案');await page.locator('.skill-card').first().getByRole('button').click();
 await page.getByRole('textbox',{name:'你的需求'}).fill('延迟提交验收');
 const submitted=page.waitForResponse(response=>response.url().endsWith('/social-copywriting/run'));
 await page.getByRole('dialog').getByRole('button',{name:'运行技能',exact:true}).click();
 await page.getByRole('button',{name:'关闭弹窗'}).click();await page.getByRole('button',{name:'账号',exact:true}).click();
 const delayedJob=await (await submitted).json();await page.waitForTimeout(200);
 const records=await (await fetch(`${base}/__qa/records`)).json();
 assert.equal(records.requests.filter(row=>row.path===`/api/jobs/${delayedJob.job_id}/events`).length,0,'切页后的延迟POST不能创建遗留SSE监听');
 await fetch(`${base}/__qa/seed-history`,{method:'POST'});
 await page.reload();await page.getByRole('button',{name:'技能库',exact:true}).click();
 const history=page.getByRole('region',{name:'最近内容技能任务'});await history.waitFor({timeout:4000});
 assert.equal(await history.getByText('不应展示的聊天内容').count(),0,'历史不应展示聊天任务内容');
 const complete=history.locator('[data-job-id="history-success"]');await complete.getByRole('button',{name:'查看结果'}).click();
 const dialog=page.getByRole('dialog');await dialog.getByText('历史成品待核对',{exact:true}).waitFor();
 const artifact=await dialog.getByRole('link',{name:'历史成品文件'}).getAttribute('href');
 assert.equal((await fetch(new URL(artifact,base))).status,200,'恢复的历史产物仍可下载');
 await dialog.getByRole('button',{name:'关闭弹窗'}).click();
 await history.locator('[data-job-id="history-running"]').getByRole('button',{name:'查看任务'}).click();
 await dialog.getByText(/执行技能 · 已用/).waitFor();
 await dialog.getByRole('button',{name:'停止接收结果',exact:true}).click();
 await dialog.getByRole('alert').getByText('用户已取消任务',{exact:true}).waitFor();
 const final=await (await fetch(`${base}/__qa/records`)).json();
 assert.equal(final.jobs.find(row=>row.id==='history-running').status,'cancelled','恢复后的停止操作应作用于持久任务');
 console.log('Skills history: late POST creates no orphan SSE; refresh restores text/artifacts/running progress and stop-result control.');
}finally{await browser.close();}
