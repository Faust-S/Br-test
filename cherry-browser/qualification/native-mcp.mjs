// SPDX-License-Identifier: AGPL-3.0-only
import { Client } from '@modelcontextprotocol/client'
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio'
import electron from 'electron'
import { createServer } from 'node:http'
import { once } from 'node:events'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import assert from 'node:assert/strict'

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..')
const fixture=createServer((request,response)=>{
  response.setHeader('Content-Type','text/html; charset=utf-8')
  if(request.url==='/second'){response.end('<title>Second</title><h1>Second page</h1>');return}
  response.end(`<!doctype html><meta charset="utf-8"><title>IWXA fixture</title>
    <h1>Application</h1><label for="name">Name</label><input id="name">
    <label for="kind">Kind</label><select id="kind"><option value="a">Alpha</option><option value="b">Beta</option></select>
    <button id="save">Save</button><p role="status" id="result">Not saved</p>
    <a href="/second">Second page</a><script>
      document.getElementById('save').addEventListener('click',()=>{
        document.getElementById('result').textContent='Saved '+document.getElementById('name').value+' '+document.getElementById('kind').value
      })
    </script>`)
})
fixture.listen(0,'127.0.0.1');await once(fixture,'listening')
const origin='http://127.0.0.1:'+fixture.address().port
const client=new Client({name:'iwxa-native-browser-qualification',version:'0.6.7'})
const transport=new StdioClientTransport({command:electron,args:[path.join(root,'dist/qualification-main.mjs'),'--fixture-origin='+origin],cwd:root,stderr:'inherit',env:Object.fromEntries(['DISPLAY','XAUTHORITY','XDG_RUNTIME_DIR'].filter(k=>typeof process.env[k]==='string').map(k=>[k,process.env[k]]))})
const passed=[]
const check=(label,fn)=>Promise.resolve().then(fn).then(value=>{passed.push(label);return value})
async function tool(name,args={}){
  const result=await client.callTool({name,arguments:args},{timeout:45000})
  assert.equal(result.isError===true,false, name+': '+JSON.stringify(result.content).slice(0,1000))
  return result
}
async function json(name,args={}){
  const result=await tool(name,args)
  const content=result.content.find(part=>part.type==='text')
  assert.ok(content)
  const value=JSON.parse(content.text)
  assert.notEqual(value.ok,false)
  return value
}
async function reject(name,args,reason){
  const result=await client.callTool({name,arguments:args},{timeout:10000})
  assert.equal(result.isError,true)
  assert.match(JSON.stringify(result.content),new RegExp(reason))
}
try{
  await client.connect(transport)
  await check('original_tool_catalog',async()=>{
    const {tools}=await client.listTools()
    assert.equal(tools.length,20)
    for(const name of ['open','snapshot','find','click','type','screenshot','go_back','go_forward','list_web_tools','call_web_tool','execute'])assert.ok(tools.some(tool=>tool.name===name))
  })
  const {tabId}=await check('navigate_visible_guest',()=>json('open',{url:origin+'/'}))
  await check('one_visible_tab',async()=>{const tabs=await json('list_tabs');assert.equal(tabs.length,1);assert.equal(tabs[0].tabId,tabId)})
  await check('actual_accessibility_tree',async()=>{const value=await json('snapshot',{tabId,full:true});assert.match(value.snapshot,/Application/);assert.match(value.snapshot,/Name/)})
  async function ref(role,name){const value=await json('find',{tabId,role,name});assert.equal(value.matches.length,1);assert.match(value.matches[0].ref,/^e[1-9]\d*$/);return value.matches[0].ref}
  await check('actual_form_input',async()=>{await json('type',{tabId,ref:await ref('textbox','Name'),text:'Alice',clear:true});await json('select_option',{tabId,ref:await ref('combobox','Kind'),values:['b']})})
  await check('click_and_readback',async()=>{await json('click',{tabId,ref:await ref('button','Save')});const result=await json('snapshot',{tabId,full:true});assert.match(result.snapshot,/Saved Alice b/)})
  await check('native_screenshot',async()=>{const result=await tool('screenshot',{tabId});const png=result.content.find(part=>part.type==='image');assert.equal(png?.mimeType,'image/png');assert.equal(Buffer.from(png.data,'base64').subarray(0,8).toString('hex'),'89504e470d0a1a0a')})
  await check('navigate_and_history',async()=>{const second=await json('open',{url:origin+'/second'});assert.equal(second.tabId,tabId);await json('go_back',{tabId});assert.match((await json('snapshot',{tabId,full:true})).snapshot,/Application/);await json('go_forward',{tabId});assert.match((await json('snapshot',{tabId,full:true})).snapshot,/Second page/)})
  await check('foreign_tab_rejected',()=>reject('snapshot',{tabId:'wrong'},'not_found'))
  await check('host_policy_rechecked',()=>reject('execute',{tabId,code:'1'},'not_allowed'))
  console.log(JSON.stringify({status:'PASS',checks:passed}))
}finally{
  await client.close().catch(()=>{})
  fixture.closeAllConnections();fixture.close()
}
