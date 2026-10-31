import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
window.__reactResult={done:false};
function App(){const [n,setN]=useState(0);return <div><button id="react-btn" onClick={()=>setN(x=>x+1)}>React count</button><output id="react-value">{n}</output></div>}
createRoot(document.getElementById('react')).render(<App/>);
let attempts=0;
const timer=setInterval(()=>{const b=document.getElementById('react-btn');if(b){clearInterval(timer);b.click();setTimeout(()=>{const v=document.getElementById('react-value').textContent;window.__reactResult={done:true,status:v==='1'?'PASS':'FAIL',value:v,react:React.version}},100)}else if(++attempts>100){clearInterval(timer);window.__reactResult={done:true,status:'FAIL',reason:'not mounted'}}},50);
